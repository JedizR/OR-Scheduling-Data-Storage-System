import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Integer, String, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Equipment(Base, TimestampMixin):
    __tablename__ = "equipment"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AVAILABLE','IN_USE','STERILIZING','MAINTENANCE','RETIRED')",
            name="ck_equipment_status",
        ),
    )

    equipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    serial_number: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    equipment_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="AVAILABLE")
    sterilization_duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sterilization_end: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    equipment_reservations: Mapped[list["EquipmentReservation"]] = relationship(
        "EquipmentReservation", back_populates="equipment"
    )
    equipment_schedules: Mapped[list["EquipmentSchedule"]] = relationship(
        "EquipmentSchedule", back_populates="equipment"
    )

    def __repr__(self) -> str:
        return f"<Equipment {self.serial_number} ({self.equipment_type})>"
