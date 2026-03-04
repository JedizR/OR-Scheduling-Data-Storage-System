"""
Exception hierarchy for all OR scheduling operations.
Each exception maps to a specific business rule violation.
"""


class SchedulingError(Exception):
    """Base class for all scheduling operation failures."""


class PatientNotFoundError(SchedulingError):
    """Patient HN not found in the system."""


class CaseNotFoundError(SchedulingError):
    """Referenced Case does not exist."""


class AppointmentNotFoundError(SchedulingError):
    """Referenced Appointment does not exist."""


class StaffNotFoundError(SchedulingError):
    """Referenced Staff member does not exist."""


class RoomNotFoundError(SchedulingError):
    """Referenced Room does not exist."""


class EquipmentNotFoundError(SchedulingError):
    """Referenced Equipment unit does not exist."""


class RoomNotActiveError(SchedulingError):
    """Room is deactivated and cannot accept new bookings."""


class RoomNotScheduledError(SchedulingError):
    """No schedule entry covers the requested date/time window."""


class RoomConflictError(SchedulingError):
    """Room already has an active reservation overlapping the requested window."""


class StaffNotActiveError(SchedulingError):
    """Staff member is deactivated."""


class StaffNotAvailableError(SchedulingError):
    """Staff member is on leave or has a conflicting reservation."""


class EquipmentNotAvailableError(SchedulingError):
    """Equipment is in maintenance, retired, or already booked in the requested window."""


class AppointmentStateError(SchedulingError):
    """The requested operation is invalid for the appointment's current status."""


class OptimisticLockError(SchedulingError):
    """Version mismatch detected — the appointment was modified by another transaction."""


class AuthorizationError(SchedulingError):
    """The staff member does not have permission to perform this operation."""
