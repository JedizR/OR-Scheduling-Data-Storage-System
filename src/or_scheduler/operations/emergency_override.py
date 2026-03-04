"""
Operation 4: Emergency Override (Preempt Elective Appointments)

When a trauma or emergency case needs immediate OR access:
1. Lock the target room with NOWAIT (emergency cannot wait in a queue)
2. Find and bump all conflicting elective appointments
3. Create the emergency appointment with full resource reservations
4. Record the complete override audit trail

One Override can displace 1..N appointments (a major trauma may need
a room, a C-arm, and a vascular surgeon — all currently booked).
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    AuditLog,
    Equipment,
    EquipmentReservation,
    Override,
    OverrideDisplacedAppointment,
    Room,
    RoomReservation,
    Staff,
    StaffReservation,
    StaffSchedule,
)
from .create_appointment import StaffItem, _to_timestamptz
from .exceptions import (
    EquipmentNotAvailableError,
    EquipmentNotFoundError,
    RoomNotActiveError,
    RoomNotFoundError,
    SchedulingError,
    StaffNotAvailableError,
    StaffNotFoundError,
)


@dataclass
class OverrideResult:
    override_id: uuid.UUID
    emergency_appointment_id: uuid.UUID
    displaced_appointment_ids: list[uuid.UUID] = field(default_factory=list)
    bumped_count: int = 0


def emergency_override(
    session: Session,
    *,
    case_id: uuid.UUID,
    room_id: uuid.UUID,
    scheduled_date: date,
    start_time: time,
    end_time: time,
    staff_items: list[StaffItem],
    equipment_ids: list[uuid.UUID] | None = None,
    authorized_by: uuid.UUID,
    authorization_code: str | None = None,
    override_reason: str,
    clinical_urgency_score: int | None = None,
) -> OverrideResult:
    """
    Preempt elective appointments to make room for an emergency case.

    Uses SELECT FOR UPDATE NOWAIT — if the room lock is held by another
    transaction, fails immediately (does not wait). The caller should
    try the next available room.

    Args:
        session: Active session (caller manages transaction).
        case_id: UUID of the emergency Case.
        room_id: UUID of the room to preempt.
        scheduled_date, start_time, end_time: Requested time window.
        staff_items: Emergency surgical team.
        equipment_ids: Required equipment.
        authorized_by: Senior physician authorizing the override.
        authorization_code: Hospital emergency system reference.
        override_reason: Clinical justification.
        clinical_urgency_score: ESI triage score if available.

    Returns:
        OverrideResult with override_id, emergency appointment_id, and bumped IDs.

    Raises:
        SchedulingError: If room lock cannot be acquired immediately (NOWAIT).
        RoomNotActiveError, RoomNotFoundError: Room validation failures.
    """
    if equipment_ids is None:
        equipment_ids = []

    reservation_start = _to_timestamptz(scheduled_date, start_time)
    reservation_end = _to_timestamptz(scheduled_date, end_time)

    # ── Step 1: Lock Room with NOWAIT ─────────────────────────────────────────
    # Emergencies cannot wait — if the room row is locked by another transaction,
    # raise immediately so the caller can try the next room.
    try:
        room = session.execute(
            select(Room)
            .where(Room.room_id == room_id)
            .with_for_update(nowait=True)
        ).scalar_one_or_none()
    except OperationalError:
        raise SchedulingError(
            f"Room {room_id} is currently locked by another transaction. "
            "Try the next available room."
        )

    if room is None:
        raise RoomNotFoundError(f"Room {room_id} not found.")
    if not room.is_active:
        raise RoomNotActiveError(f"Room {room.room_code} is deactivated.")

    # ── Step 2: Find conflicting appointments and lock them ───────────────────
    conflicting_reservations = session.execute(
        select(RoomReservation).where(
            RoomReservation.room_id == room_id,
            RoomReservation.status.notin_(["RELEASED", "COMPLETED"]),
            RoomReservation.reservation_start < reservation_end,
            RoomReservation.reservation_end > reservation_start,
        )
    ).scalars().all()

    conflicting_appointment_ids = [rr.appointment_id for rr in conflicting_reservations]

    # Lock all conflicting appointments
    conflicting_appointments = []
    for appt_id in conflicting_appointment_ids:
        appt = session.execute(
            select(Appointment)
            .where(Appointment.appointment_id == appt_id)
            .with_for_update()
        ).scalar_one_or_none()
        if appt is not None:
            conflicting_appointments.append(appt)

    # ── Step 3: Bump each conflicting appointment ─────────────────────────────
    bumped_ids = []
    txn_id = session.execute(text("SELECT txid_current()")).scalar()

    for appt in conflicting_appointments:
        old_status = appt.status

        # Release all resources for this appointment
        for rr in session.execute(
            select(RoomReservation).where(RoomReservation.appointment_id == appt.appointment_id)
        ).scalars().all():
            rr.status = "RELEASED"

        for sr in session.execute(
            select(StaffReservation).where(StaffReservation.appointment_id == appt.appointment_id)
        ).scalars().all():
            sr.status = "RELEASED"

        for er in session.execute(
            select(EquipmentReservation).where(
                EquipmentReservation.appointment_id == appt.appointment_id
            )
        ).scalars().all():
            er.status = "RELEASED"

        appt.status = "BUMPED"
        appt.version += 1

        session.add(AuditLog(
            entity_type="APPOINTMENT",
            entity_id=appt.appointment_id,
            action="BUMPED",
            old_status=old_status,
            new_status="BUMPED",
            changed_by=authorized_by,
            transaction_id=txn_id,
            notes=f"Displaced by emergency override. Reason: {override_reason}",
        ))
        bumped_ids.append(appt.appointment_id)

    session.flush()

    # ── Steps 4–7: Create emergency appointment and all reservations ──────────
    emergency_appt = Appointment(
        case_id=case_id,
        scheduled_date=scheduled_date,
        start_time=start_time,
        end_time=end_time,
        status="CONFIRMED",
        version=1,
        confirmed_by=authorized_by,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(emergency_appt)
    session.flush()

    # Room reservation for emergency
    session.add(RoomReservation(
        appointment_id=emergency_appt.appointment_id,
        room_id=room_id,
        status="CONFIRMED",
        reservation_start=reservation_start,
        reservation_end=reservation_end,
    ))

    # Equipment reservations — sorted ASC for canonical lock order
    for eq_id in sorted(equipment_ids):
        equip = session.execute(
            select(Equipment)
            .where(Equipment.equipment_id == eq_id)
            .with_for_update(nowait=True)
        ).scalar_one_or_none()
        if equip is None:
            raise EquipmentNotFoundError(f"Equipment {eq_id} not found.")
        if equip.status in ("MAINTENANCE", "RETIRED"):
            raise EquipmentNotAvailableError(
                f"Equipment {equip.serial_number} is not available (status: {equip.status})."
            )
        session.add(EquipmentReservation(
            appointment_id=emergency_appt.appointment_id,
            equipment_id=eq_id,
            status="CONFIRMED",
            reservation_start=reservation_start,
            reservation_end=reservation_end,
        ))

    # Staff reservations — sorted ASC for canonical lock order
    for item in sorted(staff_items, key=lambda x: x.staff_id):
        staff = session.execute(
            select(Staff)
            .where(Staff.staff_id == item.staff_id)
            .with_for_update(nowait=True)
        ).scalar_one_or_none()
        if staff is None:
            raise StaffNotFoundError(f"Staff {item.staff_id} not found.")
        session.add(StaffReservation(
            appointment_id=emergency_appt.appointment_id,
            staff_id=item.staff_id,
            role_in_case=item.role_in_case,
            status="CONFIRMED",
            reservation_start=reservation_start,
            reservation_end=reservation_end,
        ))

    session.flush()

    # ── Step 8: INSERT Override record ────────────────────────────────────────
    override = Override(
        emergency_appointment_id=emergency_appt.appointment_id,
        authorized_by=authorized_by,
        authorization_code=authorization_code,
        override_reason=override_reason,
        clinical_urgency_score=clinical_urgency_score,
    )
    session.add(override)
    session.flush()

    # ── Step 9: INSERT junction rows for each displaced appointment ───────────
    for appt_id in bumped_ids:
        session.add(OverrideDisplacedAppointment(
            override_id=override.override_id,
            appointment_id=appt_id,
        ))

    # ── Step 10: Write override audit log ─────────────────────────────────────
    session.add(AuditLog(
        entity_type="OVERRIDE",
        entity_id=override.override_id,
        action="OVERRIDE",
        old_status=None,
        new_status="CONFIRMED",
        changed_by=authorized_by,
        transaction_id=txn_id,
        notes=(
            f"Emergency case {case_id} preempted {len(bumped_ids)} elective appointment(s). "
            f"Reason: {override_reason}. "
            f"Auth code: {authorization_code}. "
            f"Urgency score: {clinical_urgency_score}."
        ),
    ))

    return OverrideResult(
        override_id=override.override_id,
        emergency_appointment_id=emergency_appt.appointment_id,
        displaced_appointment_ids=bumped_ids,
        bumped_count=len(bumped_ids),
    )
