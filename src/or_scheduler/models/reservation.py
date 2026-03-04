import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class RoomReservation(Base, TimestampMixin):
    """
    Time-range claim on a single Room for one Appointment.
    reservation_start / reservation_end are denormalized from the Appointment
    to enable the GIST exclusion constraint without a subquery.
    """

    __tablename__ = "room_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('HELD','CONFIRMED','RELEASED','COMPLETED')",
            name="ck_room_res_status",
        ),
        UniqueConstraint("appointment_id", "room_id", name="uq_room_res_appt_room"),
        # GIST exclusion constraint is added via DDL in scripts/init_db.py
        # because SQLAlchemy ORM does not support EXCLUDE natively.
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.appointment_id"), nullable=False
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.room_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="HELD")
    # Denormalized for GIST constraint — set equal to appointment's scheduled_date+time
    reservation_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reservation_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="NOW()"
    )

    # Relationships
    appointment: Mapped["Appointment"] = relationship(
        "Appointment", back_populates="room_reservations"
    )
    room: Mapped["Room"] = relationship("Room", back_populates="room_reservations")

    def __repr__(self) -> str:
        return f"<RoomReservation room={self.room_id} {self.reservation_start}–{self.reservation_end}>"


class StaffReservation(Base, TimestampMixin):
    __tablename__ = "staff_reservations"
    __table_args__ = (
        CheckConstraint(
            "role_in_case IN ('SURGEON','ANAESTHESIOLOGIST','SCRUB_NURSE')",
            name="ck_staff_res_role",
        ),
        CheckConstraint(
            "status IN ('HELD','CONFIRMED','RELEASED','COMPLETED')",
            name="ck_staff_res_status",
        ),
        UniqueConstraint(
            "appointment_id", "staff_id", "role_in_case", name="uq_staff_res_appt_staff_role"
        ),
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.appointment_id"), nullable=False
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.staff_id"), nullable=False
    )
    role_in_case: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="HELD")
    reservation_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reservation_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="NOW()"
    )

    # Relationships
    appointment: Mapped["Appointment"] = relationship(
        "Appointment", back_populates="staff_reservations"
    )
    staff: Mapped["Staff"] = relationship("Staff", back_populates="staff_reservations")

    def __repr__(self) -> str:
        return f"<StaffReservation staff={self.staff_id} {self.role_in_case}>"


class EquipmentReservation(Base, TimestampMixin):
    __tablename__ = "equipment_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('HELD','CONFIRMED','RELEASED','COMPLETED')",
            name="ck_equip_res_status",
        ),
        UniqueConstraint("appointment_id", "equipment_id", name="uq_equip_res_appt_equip"),
    )

    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.appointment_id"), nullable=False
    )
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("equipment.equipment_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="HELD")
    reservation_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    reservation_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="NOW()"
    )

    # Relationships
    appointment: Mapped["Appointment"] = relationship(
        "Appointment", back_populates="equipment_reservations"
    )
    equipment: Mapped["Equipment"] = relationship(
        "Equipment", back_populates="equipment_reservations"
    )

    def __repr__(self) -> str:
        return f"<EquipmentReservation equip={self.equipment_id}>"
