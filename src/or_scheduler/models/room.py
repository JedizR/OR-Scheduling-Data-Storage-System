import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Room(Base, TimestampMixin):
    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint(
            "room_type IN ('OR','EMERGENCY','HYBRID')",
            name="ck_room_type",
        ),
    )

    room_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    room_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    room_type: Mapped[str] = mapped_column(String(20), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.department_id"), nullable=True
    )
    is_laminar_flow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    department: Mapped["Department | None"] = relationship(
        "Department", back_populates="rooms"
    )
    room_reservations: Mapped[list["RoomReservation"]] = relationship(
        "RoomReservation", back_populates="room"
    )
    room_schedules: Mapped[list["RoomSchedule"]] = relationship(
        "RoomSchedule", back_populates="room"
    )

    def __repr__(self) -> str:
        return f"<Room {self.room_code} ({self.room_type})>"
