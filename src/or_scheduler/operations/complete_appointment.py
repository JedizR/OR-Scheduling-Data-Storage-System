"""
Operation 5: Complete Appointment (Surgery Done)

Marks the appointment as completed, releases all resources,
and sets equipment to STERILIZING with the appropriate lead time.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    AuditLog,
    Case,
    Equipment,
    EquipmentReservation,
    RoomReservation,
    StaffReservation,
)
from .exceptions import AppointmentNotFoundError, AppointmentStateError


def complete_appointment(
    session: Session,
    *,
    appointment_id: uuid.UUID,
    completed_by: uuid.UUID,
    actual_end_time: datetime | None = None,
    notes: str | None = None,
) -> None:
    """
    Mark an appointment as completed and release all resources.

    For each piece of equipment used, set status to STERILIZING and record
    the sterilization end time based on the equipment's sterilization_duration_min.

    Args:
        session: Active session (caller manages transaction).
        appointment_id: UUID of the appointment to complete.
        completed_by: Staff UUID of whoever is marking it complete.
        actual_end_time: When the surgery actually ended (defaults to now).
        notes: Optional completion notes.

    Raises:
        AppointmentNotFoundError: If appointment does not exist.
        AppointmentStateError: If appointment is not IN_PROGRESS or CONFIRMED.
    """
    if actual_end_time is None:
        actual_end_time = datetime.now(timezone.utc)

    # Step 1: Lock appointment
    appointment = session.execute(
        select(Appointment)
        .where(Appointment.appointment_id == appointment_id)
        .with_for_update()
    ).scalar_one_or_none()

    if appointment is None:
        raise AppointmentNotFoundError(f"Appointment {appointment_id} not found.")

    if appointment.status not in ("IN_PROGRESS", "CONFIRMED"):
        raise AppointmentStateError(
            f"Cannot complete appointment in status '{appointment.status}'. "
            "Must be IN_PROGRESS or CONFIRMED."
        )

    old_status = appointment.status

    # Step 2: Mark appointment completed
    appointment.status = "COMPLETED"
    appointment.version += 1

    # Step 3: Mark room reservation completed
    room_reservations = session.execute(
        select(RoomReservation).where(RoomReservation.appointment_id == appointment_id)
    ).scalars().all()
    for rr in room_reservations:
        rr.status = "COMPLETED"

    # Step 4: Mark staff reservations completed
    staff_reservations = session.execute(
        select(StaffReservation).where(StaffReservation.appointment_id == appointment_id)
    ).scalars().all()
    for sr in staff_reservations:
        sr.status = "COMPLETED"

    # Step 5: Mark equipment reservations completed + set equipment to STERILIZING
    equipment_reservations = session.execute(
        select(EquipmentReservation).where(
            EquipmentReservation.appointment_id == appointment_id
        )
    ).scalars().all()

    for er in equipment_reservations:
        er.status = "COMPLETED"
        equip = session.execute(
            select(Equipment)
            .where(Equipment.equipment_id == er.equipment_id)
            .with_for_update()
        ).scalar_one_or_none()
        if equip is not None and equip.sterilization_duration_min > 0:
            equip.status = "STERILIZING"
            equip.last_sterilization_end = actual_end_time + timedelta(
                minutes=equip.sterilization_duration_min
            )
        elif equip is not None:
            equip.status = "AVAILABLE"

    # Step 6: Check if all appointments for this case are done
    case = session.execute(
        select(Case).where(Case.case_id == appointment.case_id).with_for_update()
    ).scalar_one_or_none()
    if case is not None:
        all_appointments = session.execute(
            select(Appointment).where(Appointment.case_id == case.case_id)
        ).scalars().all()
        all_done = all(
            a.status in ("COMPLETED", "CANCELLED", "BUMPED")
            for a in all_appointments
        )
        if all_done:
            case.status = "COMPLETED"

    # Step 7: Write audit log
    txn_id = session.execute(text("SELECT txid_current()")).scalar()
    session.add(AuditLog(
        entity_type="APPOINTMENT",
        entity_id=appointment_id,
        action="COMPLETED",
        old_status=old_status,
        new_status="COMPLETED",
        changed_by=completed_by,
        transaction_id=txn_id,
        notes=notes or f"Surgery completed at {actual_end_time.isoformat()}",
    ))
