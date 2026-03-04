import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin


class Override(Base):
    __tablename__ = "overrides"

    override_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    emergency_appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("appointments.appointment_id"), nullable=False
    )
    authorized_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.staff_id"), nullable=False
    )
    authorization_code: Mapped[str | None] = mapped_column(String(50))
    override_reason: Mapped[str] = mapped_column(Text, nullable=False)
    clinical_urgency_score: Mapped[int | None] = mapped_column(Integer)
    override_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="NOW()"
    )

    # Relationships
    emergency_appointment: Mapped["Appointment"] = relationship(
        "Appointment",
        back_populates="override_as_emergency",
        foreign_keys=[emergency_appointment_id],
    )
    authorized_by_staff: Mapped["Staff"] = relationship(
        "Staff", foreign_keys=[authorized_by]
    )
    displaced_links: Mapped[list["OverrideDisplacedAppointment"]] = relationship(
        "OverrideDisplacedAppointment", back_populates="override"
    )

    def __repr__(self) -> str:
        return f"<Override {self.override_id}>"


class OverrideDisplacedAppointment(Base):
    """Junction table: one Override displaces 1..N Appointments."""

    __tablename__ = "override_displaced_appointments"

    override_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("overrides.override_id"),
        primary_key=True,
    )
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("appointments.appointment_id"),
        primary_key=True,
    )

    # Relationships
    override: Mapped["Override"] = relationship(
        "Override", back_populates="displaced_links"
    )
    appointment: Mapped["Appointment"] = relationship("Appointment")


class AuditLog(Base):
    """
    Append-only audit trail. NEVER UPDATE OR DELETE rows.
    BIGSERIAL PK for sequential inserts (avoids UUID random B-tree page splits).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "action IN ('CREATED','UPDATED','CONFIRMED','CANCELLED',"
            "'BUMPED','RELEASED','COMPLETED','OVERRIDE')",
            name="ck_audit_action",
        ),
        Index("idx_audit_entity", "entity_type", "entity_id", "changed_at"),
        Index("idx_audit_transaction", "transaction_id"),
    )

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30), nullable=False)
    changed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("staff.staff_id"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default="NOW()"
    )
    transaction_id: Mapped[int | None] = mapped_column(BigInteger)
    ip_address: Mapped[str | None] = mapped_column(INET)
    notes: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<AuditLog {self.log_id} {self.entity_type} {self.action}>"
