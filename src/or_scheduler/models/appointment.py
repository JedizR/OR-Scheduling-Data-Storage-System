import uuid
from datetime import date, datetime, time

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, String, Time, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('TENTATIVE','CONFIRMED','IN_PROGRESS','BUMPED','COMPLETED','CANCELLED')",
            name="ck_appointment_status",
        ),
        CheckConstraint("end_time > start_time", name="ck_appointment_times_valid"),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.case_id"), nullable=False
    )
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="TENTATIVE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.staff_id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    # Relationships
    case: Mapped["Case"] = relationship("Case", back_populates="appointments")
    confirmed_by_staff: Mapped["Staff | None"] = relationship(
        "Staff", foreign_keys=[confirmed_by]
    )
    room_reservations: Mapped[list["RoomReservation"]] = relationship(
        "RoomReservation", back_populates="appointment"
    )
    staff_reservations: Mapped[list["StaffReservation"]] = relationship(
        "StaffReservation", back_populates="appointment"
    )
    equipment_reservations: Mapped[list["EquipmentReservation"]] = relationship(
        "EquipmentReservation", back_populates="appointment"
    )
    override_as_emergency: Mapped["Override | None"] = relationship(
        "Override",
        back_populates="emergency_appointment",
        foreign_keys="Override.emergency_appointment_id",
    )

    def __repr__(self) -> str:
        return f"<Appointment {self.appointment_id} {self.scheduled_date} {self.status}>"
