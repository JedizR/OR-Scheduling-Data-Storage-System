"""
Operation 2: Create Appointment (Book the OR) — THE CORE OPERATION

This operation atomically reserves:
  - One operating room
  - All required staff members (surgeon, anaesthesiologist, scrub nurse)
  - All required equipment units

All three resource types are committed together or none are.
Race condition prevention via:
  Layer 1: SELECT FOR UPDATE (pessimistic row-level locking)
  Layer 2: GIST exclusion constraint on room_reservations (database-level safety net)

Lock order to prevent deadlock:
  Room → Equipment IDs (sorted ASC) → Staff IDs (sorted ASC)
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    AuditLog,
    Case,
    Equipment,
    EquipmentReservation,
    EquipmentSchedule,
    Room,
    RoomReservation,
    RoomSchedule,
    Staff,
    StaffReservation,
    StaffSchedule,
)
from .exceptions import (
    AppointmentStateError,
    CaseNotFoundError,
    EquipmentNotAvailableError,
    EquipmentNotFoundError,
    RoomConflictError,
    RoomNotActiveError,
    RoomNotFoundError,
    RoomNotScheduledError,
    StaffNotActiveError,
    StaffNotAvailableError,
    StaffNotFoundError,
)


@dataclass
class StaffItem:
    staff_id: uuid.UUID
    role_in_case: str  # SURGEON | ANAESTHESIOLOGIST | SCRUB_NURSE


@dataclass
class AppointmentResult:
    appointment_id: uuid.UUID
    status: str
    version: int
    scheduled_date: date
    start_time: time
    end_time: time
    room_reservation_id: uuid.UUID
    staff_reservation_ids: list[uuid.UUID] = field(default_factory=list)
    equipment_reservation_ids: list[uuid.UUID] = field(default_factory=list)


def _to_timestamptz(d: date, t: time) -> datetime:
    """Combine date + time into a timezone-aware UTC datetime."""
    return datetime(d.year, d.month, d.day, t.hour, t.minute, t.second,
                    tzinfo=timezone.utc)


def create_appointment(
    session: Session,
    *,
    case_id: uuid.UUID,
    room_id: uuid.UUID,
    scheduled_date: date,
    start_time: time,
    end_time: time,
    staff_items: list[StaffItem],
    equipment_ids: list[uuid.UUID] | None = None,
    confirmed_by: uuid.UUID,
) -> AppointmentResult:
    """
    Book an OR slot by atomically reserving room, staff, and equipment.

    All validations and inserts happen within a single transaction (caller's).
    Uses SELECT FOR UPDATE to serialize concurrent booking attempts.

    Args:
        session: Active session. Caller must commit/rollback.
        case_id: UUID of the Case being scheduled.
        room_id: UUID of the Room to book.
        scheduled_date: Date of the surgery.
        start_time: Procedure start time.
        end_time: Procedure end time.
        staff_items: List of StaffItem(staff_id, role_in_case).
        equipment_ids: Optional list of Equipment UUIDs.
        confirmed_by: Staff UUID of the coordinator confirming the booking.

    Returns:
        AppointmentResult with created appointment and reservation IDs.

    Raises:
        CaseNotFoundError, RoomNotActiveError, RoomNotScheduledError,
        RoomConflictError, StaffNotAvailableError, EquipmentNotAvailableError
    """
    if equipment_ids is None:
        equipment_ids = []

    reservation_start = _to_timestamptz(scheduled_date, start_time)
    reservation_end = _to_timestamptz(scheduled_date, end_time)

    # ── Verify Case ────────────────────────────────────────────────────────────
    case = session.execute(
        select(Case).where(Case.case_id == case_id)
    ).scalar_one_or_none()
    if case is None:
        raise CaseNotFoundError(f"Case {case_id} not found.")
    if case.status in ("COMPLETED", "CANCELLED"):
        raise AppointmentStateError(f"Case {case_id} is {case.status}; cannot add appointments.")

    # ── Step 1: Lock Room row (prevents concurrent booking of same room) ───────
    room = session.execute(
        select(Room).where(Room.room_id == room_id).with_for_update()
    ).scalar_one_or_none()
    if room is None:
        raise RoomNotFoundError(f"Room {room_id} not found.")
    if not room.is_active:
        raise RoomNotActiveError(f"Room {room.room_code} is deactivated.")

    # ── Step 2: Check room schedule covers the requested window ────────────────
    room_sched = session.execute(
        select(RoomSchedule).where(
            RoomSchedule.room_id == room_id,
            RoomSchedule.date == scheduled_date,
            RoomSchedule.available_from <= start_time,
            RoomSchedule.available_until >= end_time,
            RoomSchedule.schedule_type == "REGULAR",
        )
    ).scalar_one_or_none()
    if room_sched is None:
        raise RoomNotScheduledError(
            f"Room {room.room_code} has no REGULAR schedule covering "
            f"{start_time}–{end_time} on {scheduled_date}."
        )

    # ── Step 3: Check room overlap (no double-booking) ────────────────────────
    overlap = session.execute(
        select(RoomReservation).where(
            RoomReservation.room_id == room_id,
            RoomReservation.status.notin_(["RELEASED", "COMPLETED"]),
            RoomReservation.reservation_start < reservation_end,
            RoomReservation.reservation_end > reservation_start,
        )
    ).scalar_one_or_none()
    if overlap is not None:
        raise RoomConflictError(
            f"Room {room.room_code} is already booked from "
            f"{overlap.reservation_start.strftime('%H:%M')} to "
            f"{overlap.reservation_end.strftime('%H:%M')} on {scheduled_date}."
        )

    # ── Steps 4: Lock Equipment rows (sorted ASC for deadlock prevention) ─────
    sorted_equipment_ids = sorted(equipment_ids)
    locked_equipment = []
    for eq_id in sorted_equipment_ids:
        equip = session.execute(
            select(Equipment).where(Equipment.equipment_id == eq_id).with_for_update()
        ).scalar_one_or_none()
        if equip is None:
            raise EquipmentNotFoundError(f"Equipment {eq_id} not found.")
        if equip.status in ("MAINTENANCE", "RETIRED"):
            raise EquipmentNotAvailableError(
                f"Equipment {equip.serial_number} ({equip.equipment_type}) "
                f"is in status '{equip.status}' and cannot be booked."
            )
        # Check for overlapping equipment reservation
        eq_overlap = session.execute(
            select(EquipmentReservation).where(
                EquipmentReservation.equipment_id == eq_id,
                EquipmentReservation.status.notin_(["RELEASED", "COMPLETED"]),
                EquipmentReservation.reservation_start < reservation_end,
                EquipmentReservation.reservation_end > reservation_start,
            )
        ).scalar_one_or_none()
        if eq_overlap is not None:
            raise EquipmentNotAvailableError(
                f"Equipment {equip.serial_number} is already booked during "
                f"{start_time}–{end_time} on {scheduled_date}."
            )
        locked_equipment.append(equip)

    # ── Step 5: Lock Staff rows (sorted ASC for deadlock prevention) ──────────
    sorted_staff_items = sorted(staff_items, key=lambda x: x.staff_id)
    locked_staff = []
    for item in sorted_staff_items:
        staff = session.execute(
            select(Staff).where(Staff.staff_id == item.staff_id).with_for_update()
        ).scalar_one_or_none()
        if staff is None:
            raise StaffNotFoundError(f"Staff {item.staff_id} not found.")
        if not staff.is_active:
            raise StaffNotActiveError(f"Staff {staff.name} is deactivated.")

        # Check staff schedule covers the window
        staff_sched = session.execute(
            select(StaffSchedule).where(
                StaffSchedule.staff_id == item.staff_id,
                StaffSchedule.date == scheduled_date,
                StaffSchedule.available_from <= start_time,
                StaffSchedule.available_until >= end_time,
                StaffSchedule.schedule_type.in_(["REGULAR", "ON_CALL"]),
            )
        ).scalar_one_or_none()
        if staff_sched is None:
            raise StaffNotAvailableError(
                f"Staff {staff.name} has no schedule covering "
                f"{start_time}–{end_time} on {scheduled_date}."
            )

        # Check for overlapping staff reservation
        staff_overlap = session.execute(
            select(StaffReservation).where(
                StaffReservation.staff_id == item.staff_id,
                StaffReservation.status.notin_(["RELEASED", "COMPLETED"]),
                StaffReservation.reservation_start < reservation_end,
                StaffReservation.reservation_end > reservation_start,
            )
        ).scalar_one_or_none()
        if staff_overlap is not None:
            raise StaffNotAvailableError(
                f"Staff {staff.name} already has a conflicting reservation on {scheduled_date} "
                f"from {start_time} to {end_time}."
            )
        locked_staff.append((staff, item))

    # ── Step 6: INSERT Appointment ────────────────────────────────────────────
    appointment = Appointment(
        case_id=case_id,
        scheduled_date=scheduled_date,
        start_time=start_time,
        end_time=end_time,
        status="CONFIRMED",
        version=1,
        confirmed_by=confirmed_by,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(appointment)
    session.flush()  # get appointment_id

    # ── Step 7: INSERT RoomReservation ────────────────────────────────────────
    room_res = RoomReservation(
        appointment_id=appointment.appointment_id,
        room_id=room_id,
        status="CONFIRMED",
        reservation_start=reservation_start,
        reservation_end=reservation_end,
    )
    session.add(room_res)
    session.flush()

    # ── Step 8: INSERT EquipmentReservations ──────────────────────────────────
    equip_res_ids = []
    for equip in locked_equipment:
        er = EquipmentReservation(
            appointment_id=appointment.appointment_id,
            equipment_id=equip.equipment_id,
            status="CONFIRMED",
            reservation_start=reservation_start,
            reservation_end=reservation_end,
        )
        session.add(er)
        session.flush()
        equip_res_ids.append(er.reservation_id)

    # ── Step 9: INSERT StaffReservations ─────────────────────────────────────
    staff_res_ids = []
    for staff, item in locked_staff:
        sr = StaffReservation(
            appointment_id=appointment.appointment_id,
            staff_id=staff.staff_id,
            role_in_case=item.role_in_case,
            status="CONFIRMED",
            reservation_start=reservation_start,
            reservation_end=reservation_end,
        )
        session.add(sr)
        session.flush()
        staff_res_ids.append(sr.reservation_id)

    # ── Step 10: Update Case status ────────────────────────────────────────────
    if case.status == "OPEN":
        case.status = "SCHEDULED"

    # ── Step 11: Write audit log ──────────────────────────────────────────────
    txn_id = session.execute(text("SELECT txid_current()")).scalar()
    session.add(AuditLog(
        entity_type="APPOINTMENT",
        entity_id=appointment.appointment_id,
        action="CONFIRMED",
        old_status=None,
        new_status="CONFIRMED",
        changed_by=confirmed_by,
        transaction_id=txn_id,
        notes=(
            f"Room: {room.room_code}, "
            f"Date: {scheduled_date}, "
            f"Time: {start_time}–{end_time}, "
            f"Staff: {len(staff_res_ids)}, "
            f"Equipment: {len(equip_res_ids)}"
        ),
    ))

    return AppointmentResult(
        appointment_id=appointment.appointment_id,
        status=appointment.status,
        version=appointment.version,
        scheduled_date=appointment.scheduled_date,
        start_time=appointment.start_time,
        end_time=appointment.end_time,
        room_reservation_id=room_res.reservation_id,
        staff_reservation_ids=staff_res_ids,
        equipment_reservation_ids=equip_res_ids,
    )
