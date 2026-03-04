"""
Operation 1: Create Case

A surgeon decides a patient needs a surgical procedure. This operation creates
the clinical work order (Case) in the system.

No resource locking needed — Case creation does not touch Room, Staff
schedules, or Equipment. It is purely a clinical record creation.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import AuditLog, Case, Patient, Staff
from .exceptions import (
    CaseNotFoundError,
    PatientNotFoundError,
    StaffNotFoundError,
    SchedulingError,
)


@dataclass
class CaseResult:
    case_id: uuid.UUID
    status: str
    urgency: str
    procedure_type: str
    created_at: datetime


def create_case(
    session: Session,
    *,
    patient_hn: str,
    department_id: uuid.UUID,
    surgeon_id: uuid.UUID,
    procedure_type: str,
    urgency: str = "ELECTIVE",
    clinical_notes: str | None = None,
    estimated_duration_minutes: int | None = None,
    created_by: uuid.UUID | None = None,
) -> CaseResult:
    """
    Create a new surgical case (clinical work order).

    Args:
        session: Active SQLAlchemy session (caller manages transaction).
        patient_hn: Patient's Hospital Number from HOSxP.
        department_id: Requesting department UUID.
        surgeon_id: UUID of the surgeon initiating the case.
        procedure_type: Descriptive name of the procedure.
        urgency: 'ELECTIVE', 'URGENT', or 'EMERGENCY'.
        clinical_notes: Free-text clinical information.
        estimated_duration_minutes: Surgeon's time estimate.
        created_by: Staff ID for audit log (defaults to surgeon_id).

    Returns:
        CaseResult with the created case's details.

    Raises:
        PatientNotFoundError: If no patient with the given HN exists.
        StaffNotFoundError: If surgeon_id does not exist or is inactive.
        SchedulingError: If urgency is invalid.
    """
    if urgency not in ("ELECTIVE", "URGENT", "EMERGENCY"):
        raise SchedulingError(f"Invalid urgency '{urgency}'. Must be ELECTIVE, URGENT, or EMERGENCY.")

    # Step 1: Verify patient exists
    patient = session.execute(
        select(Patient).where(Patient.hn == patient_hn)
    ).scalar_one_or_none()
    if patient is None:
        raise PatientNotFoundError(f"No patient found with HN '{patient_hn}'.")

    # Step 2: Verify surgeon exists and is active
    surgeon = session.execute(
        select(Staff).where(Staff.staff_id == surgeon_id, Staff.is_active == True)
    ).scalar_one_or_none()
    if surgeon is None:
        raise StaffNotFoundError(f"Surgeon {surgeon_id} not found or is inactive.")

    if surgeon.role not in ("SURGEON",):
        raise SchedulingError(
            f"Staff member {surgeon.name} has role '{surgeon.role}', not SURGEON."
        )

    # Step 3: Insert Case
    case = Case(
        patient_id=patient.patient_id,
        department_id=department_id,
        initiated_by=surgeon_id,
        procedure_type=procedure_type,
        urgency=urgency,
        status="OPEN",
        clinical_notes=clinical_notes,
        estimated_duration_minutes=estimated_duration_minutes,
    )
    session.add(case)
    session.flush()  # get case_id before audit log

    # Step 4: Write audit log
    audit_by = created_by or surgeon_id
    txn_id = session.execute(text("SELECT txid_current()")).scalar()
    session.add(AuditLog(
        entity_type="CASE",
        entity_id=case.case_id,
        action="CREATED",
        old_status=None,
        new_status="OPEN",
        changed_by=audit_by,
        transaction_id=txn_id,
        notes=f"Procedure: {procedure_type}, Urgency: {urgency}",
    ))

    return CaseResult(
        case_id=case.case_id,
        status=case.status,
        urgency=case.urgency,
        procedure_type=case.procedure_type,
        created_at=case.created_at,
    )
