import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Case(Base, TimestampMixin):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint(
            "urgency IN ('ELECTIVE','URGENT','EMERGENCY')",
            name="ck_case_urgency",
        ),
        CheckConstraint(
            "status IN ('OPEN','SCHEDULED','IN_PROGRESS','COMPLETED','CANCELLED')",
            name="ck_case_status",
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("patients.patient_id"), nullable=False
    )
    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.department_id"), nullable=False
    )
    initiated_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.staff_id"), nullable=False
    )
    procedure_type: Mapped[str] = mapped_column(String(200), nullable=False)
    urgency: Mapped[str] = mapped_column(String(20), nullable=False, default="ELECTIVE")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    clinical_notes: Mapped[str | None] = mapped_column(Text)
    estimated_duration_minutes: Mapped[int | None] = mapped_column(Integer)

    # Relationships
    patient: Mapped["Patient"] = relationship("Patient", back_populates="cases")
    department: Mapped["Department"] = relationship("Department", back_populates="cases")
    initiated_by_staff: Mapped["Staff"] = relationship(
        "Staff", back_populates="cases_initiated", foreign_keys=[initiated_by]
    )
    appointments: Mapped[list["Appointment"]] = relationship(
        "Appointment", back_populates="case"
    )

    def __repr__(self) -> str:
        return f"<Case {self.case_id} {self.procedure_type} [{self.urgency}]>"
