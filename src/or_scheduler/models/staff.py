import uuid

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Staff(Base, TimestampMixin):
    __tablename__ = "staff"
    __table_args__ = (
        CheckConstraint(
            "role IN ('SURGEON','ANAESTHESIOLOGIST','SCRUB_NURSE','COORDINATOR')",
            name="ck_staff_role",
        ),
    )

    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.department_id"), nullable=False
    )
    license_number: Mapped[str | None] = mapped_column(String(50), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Relationships
    department: Mapped["Department"] = relationship("Department", back_populates="staff")
    cases_initiated: Mapped[list["Case"]] = relationship(
        "Case", back_populates="initiated_by_staff", foreign_keys="Case.initiated_by"
    )
    staff_reservations: Mapped[list["StaffReservation"]] = relationship(
        "StaffReservation", back_populates="staff"
    )
    staff_schedules: Mapped[list["StaffSchedule"]] = relationship(
        "StaffSchedule", back_populates="staff"
    )

    def __repr__(self) -> str:
        return f"<Staff {self.name} ({self.role})>"
