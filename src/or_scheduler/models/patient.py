import uuid

from sqlalchemy import CheckConstraint, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Patient(Base, TimestampMixin):
    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint("age >= 0 AND age <= 150", name="ck_patient_age"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hn: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    hosxp_ref: Mapped[str | None] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    age: Mapped[int | None] = mapped_column(Integer)
    blood_type: Mapped[str | None] = mapped_column(String(5))
    allergies: Mapped[str | None] = mapped_column(Text)

    # Relationships
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="patient")

    def __repr__(self) -> str:
        return f"<Patient HN={self.hn} {self.name}>"
