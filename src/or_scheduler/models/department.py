import uuid

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Department(Base, TimestampMixin):
    __tablename__ = "departments"

    department_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    building: Mapped[str | None] = mapped_column(String(100))
    floor: Mapped[int | None] = mapped_column(Integer)

    # Relationships
    staff: Mapped[list["Staff"]] = relationship("Staff", back_populates="department")
    rooms: Mapped[list["Room"]] = relationship("Room", back_populates="department")
    cases: Mapped[list["Case"]] = relationship("Case", back_populates="department")

    def __repr__(self) -> str:
        return f"<Department {self.name}>"
