import uuid
from datetime import date, time

from sqlalchemy import CheckConstraint, Date, ForeignKey, String, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class RoomSchedule(Base, TimestampMixin):
    __tablename__ = "room_schedules"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('REGULAR','MAINTENANCE','CLEANING')",
            name="ck_room_sched_type",
        ),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rooms.room_id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    available_from: Mapped[time] = mapped_column(Time, nullable=False)
    available_until: Mapped[time] = mapped_column(Time, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, default="REGULAR")

    # Relationships
    room: Mapped["Room"] = relationship("Room", back_populates="room_schedules")

    def __repr__(self) -> str:
        return f"<RoomSchedule room={self.room_id} {self.date}>"


class StaffSchedule(Base, TimestampMixin):
    __tablename__ = "staff_schedules"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('REGULAR','ON_CALL','LEAVE')",
            name="ck_staff_sched_type",
        ),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.staff_id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    available_from: Mapped[time] = mapped_column(Time, nullable=False)
    available_until: Mapped[time] = mapped_column(Time, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, default="REGULAR")

    # Relationships
    staff: Mapped["Staff"] = relationship("Staff", back_populates="staff_schedules")

    def __repr__(self) -> str:
        return f"<StaffSchedule staff={self.staff_id} {self.date}>"


class EquipmentSchedule(Base, TimestampMixin):
    __tablename__ = "equipment_schedules"
    __table_args__ = (
        CheckConstraint(
            "schedule_type IN ('REGULAR','MAINTENANCE','STERILIZATION')",
            name="ck_equip_sched_type",
        ),
    )

    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    equipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("equipment.equipment_id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    available_from: Mapped[time] = mapped_column(Time, nullable=False)
    available_until: Mapped[time] = mapped_column(Time, nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(20), nullable=False, default="REGULAR")

    # Relationships
    equipment: Mapped["Equipment"] = relationship("Equipment", back_populates="equipment_schedules")

    def __repr__(self) -> str:
        return f"<EquipmentSchedule equip={self.equipment_id} {self.date}>"
