"""
Operation 3: Cancel Appointment

Releases all three resource types (room, staff, equipment) atomically.
There is no intermediate state where a room is released but staff are still held.
"""

import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import (
    Appointment,
    AuditLog,
    EquipmentReservation,
    RoomReservation,
    StaffReservation,
)
from .exceptions import AppointmentNotFoundError, AppointmentStateError


def cancel_appointment(
    session: Session,
    *,
    appointment_id: uuid.UUID,
    cancelled_by: uuid.UUID,
    reason: str | None = None,
) -> None:
    """
    Cancel a confirmed appointment and release all held resources.

    Args:
        session: Active session. Caller manages transaction boundary.
        appointment_id: UUID of the appointment to cancel.
        cancelled_by: Staff UUID of whoever is cancelling.
        reason: Optional cancellation reason for the audit log.

    Raises:
        AppointmentNotFoundError: If appointment does not exist.
        AppointmentStateError: If appointment is IN_PROGRESS or COMPLETED.
    """
    # Step 1: Lock appointment row (prevents concurrent modification)
    appointment = session.execute(
        select(Appointment)
        .where(Appointment.appointment_id == appointment_id)
        .with_for_update()
    ).scalar_one_or_none()

    if appointment is None:
        raise AppointmentNotFoundError(f"Appointment {appointment_id} not found.")

    if appointment.status in ("IN_PROGRESS", "COMPLETED"):
        raise AppointmentStateError(
            f"Cannot cancel appointment in status '{appointment.status}'."
        )
    if appointment.status == "CANCELLED":
        raise AppointmentStateError("Appointment is already cancelled.")

    old_status = appointment.status

    # Step 2: Release room reservation
    session.execute(
        select(RoomReservation)
        .where(RoomReservation.appointment_id == appointment_id)
        .with_for_update()
    )
    room_reservations = session.execute(
        select(RoomReservation).where(RoomReservation.appointment_id == appointment_id)
    ).scalars().all()
    for rr in room_reservations:
        rr.status = "RELEASED"

    # Step 3: Release staff reservations
    staff_reservations = session.execute(
        select(StaffReservation).where(StaffReservation.appointment_id == appointment_id)
    ).scalars().all()
    for sr in staff_reservations:
        sr.status = "RELEASED"

    # Step 4: Release equipment reservations
    equipment_reservations = session.execute(
        select(EquipmentReservation).where(
            EquipmentReservation.appointment_id == appointment_id
        )
    ).scalars().all()
    for er in equipment_reservations:
        er.status = "RELEASED"

    # Step 5: Update appointment status
    appointment.status = "CANCELLED"
    appointment.version += 1

    # Step 6: Write audit log
    txn_id = session.execute(text("SELECT txid_current()")).scalar()
    session.add(AuditLog(
        entity_type="APPOINTMENT",
        entity_id=appointment_id,
        action="CANCELLED",
        old_status=old_status,
        new_status="CANCELLED",
        changed_by=cancelled_by,
        transaction_id=txn_id,
        notes=reason,
    ))
