# PLAN_1 — Assignment Implementation Plan

> **Scope:** ROADMAP Milestones 1–5 (full assignment completion)
> **Output:** Working system + 5 Jupyter notebooks satisfying every graded requirement

---

## Architecture Decisions

### Why Synchronous SQLAlchemy for Assignment Phase
The assignment requires threading-based isolation tests. Python's `threading` module combined with synchronous psycopg2 gives the clearest, most debuggable concurrency demonstration — each thread holds a real OS thread with a dedicated DB connection. Async SQLAlchemy (asyncpg) would require an event loop per thread, obscuring the locking behavior we're demonstrating. The full API layer (Milestone 6) will switch to async.

### GIST Constraint Design — Denormalized Time Columns
The blueprint's GIST constraint uses a subquery into `appointments`, which is non-trivial to express efficiently. The clean production pattern is to store `reservation_start TIMESTAMPTZ` and `reservation_end TIMESTAMPTZ` directly on each reservation table. This:
1. Enables a straightforward GIST exclusion: `(room_id WITH =, tstzrange(reservation_start, reservation_end) WITH &&)`
2. Eliminates subquery overhead in the constraint evaluation
3. Makes availability queries faster (no join needed for overlap check)

These columns are set equal to `(scheduled_date + start_time)::timestamptz` at INSERT time and are never updated independently.

### Lock Order (Deadlock Prevention)
All operations acquire locks in this canonical order to prevent deadlock cycles:
```
Room → Equipment IDs (sorted ASC) → Staff IDs (sorted ASC)
```

### Connection Pool for Performance Tests
- `pool_size=20`, `max_overflow=10`, `pool_timeout=30`
- Reduces connection overhead for the 10,000-operation performance test
- Each thread in isolation test gets its own connection from the pool

---

## File Structure

```
OR-Scheduling-Data-Storage-System/
├── pyproject.toml                    # uv project + all dependencies
├── .env.example                      # DATABASE_URL template
├── .env                              # actual (gitignored)
├── docker-compose.yml                # PostgreSQL 16
│
├── src/
│   └── or_scheduler/
│       ├── __init__.py
│       ├── config.py                 # pydantic-settings Settings class
│       ├── database.py               # engine, SessionLocal, get_session()
│       │
│       ├── models/
│       │   ├── __init__.py           # re-exports all models
│       │   ├── base.py               # Base, TimestampMixin
│       │   ├── department.py         # Department
│       │   ├── staff.py              # Staff
│       │   ├── room.py               # Room
│       │   ├── equipment.py          # Equipment
│       │   ├── patient.py            # Patient
│       │   ├── case.py               # Case
│       │   ├── appointment.py        # Appointment
│       │   ├── reservation.py        # RoomReservation, StaffReservation, EquipmentReservation
│       │   ├── schedule.py           # RoomSchedule, StaffSchedule, EquipmentSchedule
│       │   └── override.py           # Override, OverrideDisplacedAppointment, AuditLog
│       │
│       ├── operations/
│       │   ├── __init__.py
│       │   ├── exceptions.py         # SchedulingError hierarchy
│       │   ├── create_case.py
│       │   ├── create_appointment.py
│       │   ├── cancel_appointment.py
│       │   ├── emergency_override.py
│       │   └── complete_appointment.py
│       │
│       └── seed.py                   # seed_database()
│
├── scripts/
│   └── init_db.py                    # extensions + create_all + GIST DDL
│
├── Assignment/
│   ├── 01_schema_and_orm.ipynb
│   ├── 02_seed_data.ipynb
│   ├── 03_atomic_operations.ipynb
│   ├── 04_performance_test.ipynb
│   └── 05_isolation_test.ipynb
│
├── ROADMAP.md
└── PLAN_1.md
```

---

## Step-by-Step Implementation

---

### Step 1 — pyproject.toml + Docker

**File: `pyproject.toml`**
```toml
[project]
name = "or-scheduler"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "sqlalchemy>=2.0",
    "psycopg2-binary",
    "pydantic-settings>=2.0",
    "faker",
    "rich",
    "jupyter",
    "ipykernel",
    "python-dotenv",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/or_scheduler"]
```

**File: `docker-compose.yml`**
```yaml
version: "3.9"
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: orscheduler
      POSTGRES_PASSWORD: orscheduler
      POSTGRES_DB: orscheduler
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
volumes:
  pgdata:
```

**File: `.env.example`**
```
DATABASE_URL=postgresql://orscheduler:orscheduler@localhost:5432/orscheduler
POOL_SIZE=20
MAX_OVERFLOW=10
ECHO_SQL=false
```

---

### Step 2 — Config + Database Module

**File: `src/or_scheduler/config.py`**
```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str
    pool_size: int = 20
    max_overflow: int = 10
    echo_sql: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
```

**File: `src/or_scheduler/database.py`**
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from .config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.pool_size,
    max_overflow=settings.max_overflow,
    pool_pre_ping=True,
    echo=settings.echo_sql,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

---

### Step 3 — ORM Models

**File: `src/or_scheduler/models/base.py`**
```python
from sqlalchemy import Column, TIMESTAMP, text
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class TimestampMixin:
    created_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False,
                        server_default=text("NOW()"), onupdate=text("NOW()"))
```

**Model: `Department`** — `department_id` UUID PK, `name`, `building`, `floor`

**Model: `Staff`** — `staff_id` UUID PK, `name`, `role` (CHECK SURGEON/ANAESTHESIOLOGIST/SCRUB_NURSE/COORDINATOR), `department_id` FK, `license_number` UNIQUE nullable, `is_active`

**Model: `Room`** — `room_id` UUID PK, `room_code` UNIQUE, `room_type` (CHECK OR/EMERGENCY/HYBRID), `department_id` FK nullable, `is_laminar_flow`, `is_active`

**Model: `Equipment`** — `equipment_id` UUID PK, `serial_number` UNIQUE, `equipment_type`, `status` (CHECK AVAILABLE/IN_USE/STERILIZING/MAINTENANCE/RETIRED), `sterilization_duration_min`, `last_sterilization_end`

**Model: `Patient`** — `patient_id` UUID PK, `hn` UNIQUE NOT NULL, `hosxp_ref` UNIQUE nullable, `name`, `age` (0–150), `blood_type`, `allergies`

**Model: `Case`** — `case_id` UUID PK, `patient_id` FK, `department_id` FK, `initiated_by` FK(staff), `procedure_type`, `urgency` (ELECTIVE/URGENT/EMERGENCY), `status` (OPEN/SCHEDULED/IN_PROGRESS/COMPLETED/CANCELLED), `clinical_notes`, `estimated_duration_minutes`

**Model: `Appointment`** — `appointment_id` UUID PK, `case_id` FK, `scheduled_date` DATE, `start_time` TIME, `end_time` TIME, `status` (TENTATIVE/CONFIRMED/IN_PROGRESS/BUMPED/COMPLETED/CANCELLED), `version` INT DEFAULT 1, `confirmed_by` FK(staff) nullable, `confirmed_at`

**Critical: `RoomReservation`** (table: `room_reservations`)
```python
# Extra denormalized columns for GIST constraint
reservation_start = Column(TIMESTAMP(timezone=True), nullable=False)
reservation_end   = Column(TIMESTAMP(timezone=True), nullable=False)
status            = Column(String(20), CHECK IN ('HELD','CONFIRMED','RELEASED','COMPLETED'))

# GIST constraint added via DDL after table creation:
# EXCLUDE USING GIST (room_id WITH =,
#   tstzrange(reservation_start, reservation_end) WITH &&)
# WHERE (status NOT IN ('RELEASED', 'COMPLETED'))
```

**Model: `StaffReservation`** — `staff_id` FK, `role_in_case` (SURGEON/ANAESTHESIOLOGIST/SCRUB_NURSE), `reservation_start`, `reservation_end`, `status`

**Model: `EquipmentReservation`** — `equipment_id` FK, `reservation_start`, `reservation_end`, `status`

**Schedule models** — `RoomSchedule`, `StaffSchedule`, `EquipmentSchedule`: each has resource FK, `date`, `available_from` TIME, `available_until` TIME, `schedule_type`

**Model: `Override`** — `override_id`, `emergency_appointment_id` FK, `authorized_by` FK(staff), `authorization_code`, `override_reason`, `clinical_urgency_score`, `override_at`

**Model: `OverrideDisplacedAppointment`** — junction: `override_id` + `appointment_id` composite PK

**Model: `AuditLog`** — `log_id` BIGSERIAL PK, `entity_type`, `entity_id` UUID, `action` (CREATED/UPDATED/CONFIRMED/CANCELLED/BUMPED/RELEASED/COMPLETED/OVERRIDE), `old_status`, `new_status`, `changed_by` FK(staff), `changed_at`, `transaction_id`, `ip_address`, `notes`

---

### Step 4 — `scripts/init_db.py`

```python
"""
Creates PostgreSQL extensions, all tables, GIST constraint, indexes,
and updated_at triggers. Safe to re-run (CREATE IF NOT EXISTS).
"""
from sqlalchemy import text
from or_scheduler.database import engine
from or_scheduler.models import Base  # imports all models

EXTENSIONS = [
    "CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";",
    "CREATE EXTENSION IF NOT EXISTS \"btree_gist\";",
]

GIST_CONSTRAINT = """
ALTER TABLE room_reservations
    DROP CONSTRAINT IF EXISTS no_room_overlap;
ALTER TABLE room_reservations
    ADD CONSTRAINT no_room_overlap
    EXCLUDE USING GIST (
        room_id WITH =,
        tstzrange(reservation_start, reservation_end) WITH &&
    )
    WHERE (status NOT IN ('RELEASED', 'COMPLETED'));
"""

UPDATED_AT_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TABLES_WITH_UPDATED_AT = [
    "departments", "staff", "rooms", "equipment", "patients",
    "cases", "appointments", "room_reservations", "staff_reservations",
    "equipment_reservations", "room_schedules", "staff_schedules",
    "equipment_schedules", "overrides",
]

def main():
    with engine.connect() as conn:
        for ext in EXTENSIONS:
            conn.execute(text(ext))
        conn.commit()

    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(text(GIST_CONSTRAINT))
        conn.execute(text(UPDATED_AT_TRIGGER_FN))
        for table in TABLES_WITH_UPDATED_AT:
            conn.execute(text(f"""
                DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table};
                CREATE TRIGGER trg_{table}_updated_at
                    BEFORE UPDATE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
            """))
        conn.commit()
    print("Database initialized successfully.")

if __name__ == "__main__":
    main()
```

---

### Step 5 — Seed Data

**File: `src/or_scheduler/seed.py`**

Seed must be idempotent (check before insert). Uses `faker` with Thai locale where possible.

**Data to insert:**
```
Departments (5):
  - "ศัลยกรรมกระดูก" / Orthopaedics Surgery
  - "ศัลยกรรมหัวใจ" / Cardiac Surgery
  - "ประสาทศัลยศาสตร์" / Neurosurgery
  - "ศัลยกรรมทั่วไป" / General Surgery
  - "วิสัญญีวิทยา" / Anaesthesiology

Rooms (8):
  OR-1 through OR-6 (type=OR), HYBRID-1 (type=HYBRID), ER-1 (type=EMERGENCY)

Equipment (6):
  C-arm-001, C-arm-002 (type="C-arm Fluoroscopy", sterilization=30min)
  LAPC-001, LAPC-002 (type="Laparoscopic Tower", sterilization=45min)
  DAVINCI-001 (type="Robotic Surgical System da Vinci", sterilization=60min)
  CELLSAVER-001 (type="Cell Saver", sterilization=20min)

Staff (20):
  5 Surgeons  — 1 per dept (not anaesthesiology)
  5 Anaesthesiologists — all in Anaesthesiology dept
  5 Scrub Nurses — spread across surgical depts
  5 Coordinators — 1 per dept

Patients (100):
  Generated with Faker, realistic HN format "HN-XXXXXXXX"

Schedules (14 days from today):
  All rooms: 08:00–17:00 REGULAR daily
  All staff: 08:00–17:00 REGULAR weekdays, ON_CALL weekends
  All equipment: 08:00–17:00 REGULAR daily
```

**Return value:** `dict[str, int]` — row counts per table

---

### Step 6 — Exception Hierarchy

**File: `src/or_scheduler/operations/exceptions.py`**
```python
class SchedulingError(Exception):
    """Base class for all scheduling operation errors."""

class RoomConflictError(SchedulingError):
    """Room already reserved during the requested window."""

class RoomNotActiveError(SchedulingError):
    """Room is deactivated."""

class RoomNotScheduledError(SchedulingError):
    """No schedule entry covers the requested window."""

class StaffNotAvailableError(SchedulingError):
    """Staff member is on leave or has a conflicting reservation."""

class EquipmentNotAvailableError(SchedulingError):
    """Equipment is in maintenance, retired, or already booked."""

class AppointmentStateError(SchedulingError):
    """Operation is invalid for the appointment's current status."""

class OptimisticLockError(SchedulingError):
    """Version mismatch — concurrent modification detected."""

class CaseNotFoundError(SchedulingError):
    """Referenced Case does not exist."""

class PatientNotFoundError(SchedulingError):
    """Patient HN not found."""
```

---

### Step 7 — Atomic Operations

#### Operation 1: `create_case()`
```
Input: patient_hn, department_id, surgeon_id, procedure_type, urgency,
       clinical_notes, estimated_duration_minutes

Steps (one transaction):
1. SELECT patient WHERE hn = :hn  →  PatientNotFoundError if missing
2. SELECT staff WHERE staff_id = :surgeon_id AND is_active = TRUE  →  fail if not found
3. Verify staff.department_id == department_id  →  fail if mismatch
4. INSERT cases (status=OPEN, urgency=:urgency)
5. INSERT audit_log (action=CREATED, new_status=OPEN)
6. COMMIT

Return: CaseResult(case_id, status)
```

#### Operation 2: `create_appointment()` — THE CORE
```
Input: case_id, room_id, scheduled_date, start_time, end_time,
       staff_items: list[{staff_id, role_in_case}],
       equipment_ids: list[UUID],
       confirmed_by: staff_id

Steps (one transaction, READ COMMITTED + SELECT FOR UPDATE):

1. LOCK Room:
   SELECT * FROM rooms WHERE room_id = :id FOR UPDATE
   → RoomNotActiveError if is_active = FALSE

2. CHECK room schedule:
   SELECT 1 FROM room_schedules WHERE room_id=:id AND date=:date
   AND available_from <= :start_time AND available_until >= :end_time
   AND schedule_type = 'REGULAR'
   → RoomNotScheduledError if not found

3. CHECK room overlap:
   SELECT 1 FROM room_reservations rr
   WHERE rr.room_id = :id
   AND tstzrange(rr.reservation_start, rr.reservation_end) &&
       tstzrange(:start_ts, :end_ts)
   AND rr.status NOT IN ('RELEASED','COMPLETED')
   → RoomConflictError if any row found

4. FOR EACH equipment_id (sorted ASC):
   SELECT * FROM equipment WHERE equipment_id = :id FOR UPDATE
   CHECK no overlapping equipment_reservation
   → EquipmentNotAvailableError if conflict

5. FOR EACH staff_item (sorted by staff_id ASC):
   SELECT * FROM staff WHERE staff_id = :id FOR UPDATE
   CHECK staff_schedule covers window
   CHECK no overlapping staff_reservation
   → StaffNotAvailableError if conflict

6. INSERT appointments (status=CONFIRMED, version=1)
7. INSERT room_reservations (reservation_start, reservation_end, status=CONFIRMED)
8. INSERT equipment_reservations per equipment (status=CONFIRMED)
9. INSERT staff_reservations per staff (status=CONFIRMED)
10. INSERT audit_log (action=CONFIRMED, transaction_id=pg_current_xact_id())
11. COMMIT

Return: AppointmentResult(appointment_id, status, version)
```

#### Operation 3: `cancel_appointment()`
```
Input: appointment_id, cancelled_by: staff_id, reason: str

Steps:
1. SELECT appointment FOR UPDATE
   → AppointmentStateError if status IN ('IN_PROGRESS','COMPLETED')
2. UPDATE room_reservations SET status='RELEASED'
3. UPDATE staff_reservations SET status='RELEASED'
4. UPDATE equipment_reservations SET status='RELEASED'
5. UPDATE appointments SET status='CANCELLED', version=version+1
6. INSERT audit_log (action=CANCELLED, old_status, new_status=CANCELLED)
7. COMMIT
```

#### Operation 4: `emergency_override()`
```
Input: case_id, room_id, scheduled_date, start_time, end_time,
       staff_items, equipment_ids,
       authorized_by, authorization_code, override_reason, clinical_urgency_score

Steps:
1. LOCK Room NOWAIT:
   SELECT * FROM rooms WHERE room_id = :id FOR UPDATE NOWAIT
   → SchedulingError("room locked by concurrent transaction") if unavailable

2. FIND conflicting appointments:
   SELECT a.* FROM appointments a
   JOIN room_reservations rr ON rr.appointment_id = a.appointment_id
   WHERE rr.room_id = :id
   AND tstzrange(rr.reservation_start, rr.reservation_end) &&
       tstzrange(:start_ts, :end_ts)
   AND rr.status NOT IN ('RELEASED','COMPLETED')
   FOR UPDATE  -- lock all conflicting appointments

3. FOR EACH conflicting appointment:
   UPDATE room_reservations SET status='RELEASED'
   UPDATE staff_reservations SET status='RELEASED'
   UPDATE equipment_reservations SET status='RELEASED'
   UPDATE appointments SET status='BUMPED', version=version+1
   INSERT audit_log (action=BUMPED)

4. INSERT emergency Appointment (status=CONFIRMED)
5. INSERT room_reservation
6. INSERT staff_reservations
7. INSERT equipment_reservations
8. INSERT overrides
9. INSERT override_displaced_appointments (one row per bumped appointment)
10. INSERT audit_log (action=OVERRIDE)
11. COMMIT

Return: OverrideResult(override_id, emergency_appointment_id, displaced: list[UUID])
```

#### Operation 5: `complete_appointment()`
```
Input: appointment_id, actual_end_time: datetime, completed_by: staff_id

Steps:
1. SELECT appointment FOR UPDATE
   → AppointmentStateError if status != 'IN_PROGRESS'
2. UPDATE appointments SET status='COMPLETED', version=version+1
3. UPDATE room_reservations SET status='COMPLETED'
4. UPDATE staff_reservations SET status='COMPLETED'
5. UPDATE equipment_reservations SET status='COMPLETED'
6. UPDATE equipment SET status='STERILIZING',
   last_sterilization_end = :actual_end_time + (sterilization_duration_min * '1 minute')
   for each equipment in this appointment's equipment_reservations
7. Check if all appointments for this case are done → UPDATE cases SET status='COMPLETED'
8. INSERT audit_log (action=COMPLETED)
9. COMMIT
```

---

### Step 8 — Assignment Notebooks

#### Notebook 01: `01_schema_and_orm.ipynb`
```
Cell 1: Import models, show class definitions with Column types
Cell 2: Create engine, run create_all (or init_db)
Cell 3: Query INFORMATION_SCHEMA.tables → show all 16 tables
Cell 4: Query INFORMATION_SCHEMA.columns → show columns for key tables
Cell 5: Show GIST constraint via pg_constraint
Cell 6: Entity-relationship summary table (textual)
```

#### Notebook 02: `02_seed_data.ipynb`
```
Cell 1: Run seed_database(), print summary counts
Cell 2: SELECT * FROM departments → display with rich Table
Cell 3: SELECT COUNT(*) per table → display all counts
Cell 4: Sample patient records
Cell 5: Sample staff distribution by role
Cell 6: Room + schedule overview
```

#### Notebook 03: `03_atomic_operations.ipynb`
```
Cell 1: Setup — pick seed data references (room_id, staff_ids, equipment_ids)
Cell 2: Op 1 — create_case() → show case in DB, show audit_log entry
Cell 3: Op 2 — create_appointment() → success case → show reservation rows
Cell 4: Op 2 — intentional failure (bad room_id) → show rollback, nothing committed
Cell 5: Op 3 — cancel_appointment() → show RELEASED reservations
Cell 6: Op 4 — emergency_override() → show BUMPED + new CONFIRMED
Cell 7: Op 5 — complete_appointment() → show COMPLETED + STERILIZING equipment
Cell 8: Audit log dump — every action recorded
```

#### Notebook 04: `04_performance_test.ipynb`
```python
def test_create_case_performance(n: int = 10_000):
    """
    Insert n Cases with pre-created patients.
    Uses connection pool (pool_size=20) and batched transactions (100 per tx).
    Reports: total time, TPS, P50/P95/P99 per-op latency.
    """

def test_create_appointment_performance(n: int = 2_000, rooms: int = 8):
    """
    Create n appointments spread across rooms rooms using ThreadPoolExecutor(max_workers=20).
    Each worker uses its own DB connection from the pool.
    Reports: successes, conflicts, TPS, latency distribution.
    """
```

**Expected results (rough targets for seeded data):**
- 10,000 create_case: ~15–40 seconds → ~250–667 TPS
- P99 per create_case: < 50ms (single-row insert + audit)

#### Notebook 05: `05_isolation_test.ipynb`
```python
def test_no_double_booking(num_threads: int = 50):
    """
    50 threads all attempt to book the SAME room at the SAME time.

    Design:
    - Pre-create 50 Cases (one per thread) — deterministic UUIDs
    - Use threading.Barrier(num_threads) to synchronize all threads to start simultaneously
    - Each thread calls create_appointment(case_id=own_case, room_id=OR-1,
                                           date=target_date, 08:00-10:00, ...)
    - Collect results: successes, RoomConflictError, IntegrityError (GIST bypass)

    Assertions:
    - successes == 1  (exactly one thread wins)
    - failures == num_threads - 1  (all others get RoomConflictError)
    - SELECT COUNT(*) FROM room_reservations
        WHERE room_id=OR-1 AND status NOT IN ('RELEASED','COMPLETED') == 1

    Timeline: print thread start timestamps, success timestamp, error timestamps
    Proof: SELECT FOR UPDATE serializes access → first committer wins, rest see conflict
    """

def test_gist_safety_net():
    """
    Bypass application layer and attempt raw SQL INSERT that would double-book.
    Prove that GIST constraint rejects it with IntegrityError.
    """
```

**Expected output:**
```
[ISOLATION TEST] 50 threads targeting OR-1 at 2025-03-15 08:00–10:00
Barrier released at: 2025-03-15T08:00:00.000001
Thread-12 SUCCESS  at +0.003s  → appointment_id: abc123...
Thread-01 CONFLICT at +0.004s  → RoomConflictError: OR-1 already booked
Thread-33 CONFLICT at +0.004s  → RoomConflictError: OR-1 already booked
... (47 more conflicts)

Result: 1 success, 49 conflicts, 0 integrity errors
DB check: 1 active reservation for OR-1 at 08:00-10:00 ✓
PROOF: SELECT FOR UPDATE serialized all 50 threads. Exactly 1 wins.
```

---

## Dependency Installation Commands

```bash
# Initialize uv project
uv init --no-readme

# Add all dependencies
uv add sqlalchemy psycopg2-binary pydantic-settings faker rich python-dotenv
uv add --dev jupyter ipykernel

# Start database
docker-compose up -d

# Initialize schema
uv run python scripts/init_db.py

# Seed data
uv run python -c "from or_scheduler.seed import seed_database; print(seed_database())"

# Register kernel for Jupyter
uv run python -m ipykernel install --user --name or-scheduler
```

---

## Performance Optimization Notes

### Connection Pool Tuning
```python
engine = create_engine(
    DATABASE_URL,
    pool_size=20,          # matches ThreadPoolExecutor(max_workers=20)
    max_overflow=10,       # burst capacity
    pool_timeout=30,       # wait max 30s for connection
    pool_pre_ping=True,    # recycle stale connections
    pool_recycle=1800,     # recycle connections older than 30min
)
```

### Batch Inserts for Performance Test
For the 10,000 create_case performance test, use transaction batching:
```python
BATCH_SIZE = 100
# Each batch opens 1 transaction, inserts 100 cases + 100 audit rows → COMMIT
# 100 batches × 100 rows = 10,000 operations
# This demonstrates OLTP throughput rather than connection overhead
```

### Index Strategy (already in blueprint, implemented in init_db)
- Partial indexes exclude CANCELLED/BUMPED/COMPLETED rows → smaller index, faster scans
- Composite indexes cover the most frequent query patterns (date + status)
- GIST index on room_reservations covers the overlap check in O(log n)

---

## Quality Checklist (before marking milestone complete)

### Milestone 1
- [ ] `docker-compose up -d && python scripts/init_db.py` runs without error
- [ ] `psql` shows all 16 tables, `btree_gist` extension loaded

### Milestone 2
- [ ] All 16 tables present with correct columns and FK relationships
- [ ] GIST constraint `no_room_overlap` visible in `pg_constraint`
- [ ] `updated_at` triggers fire on UPDATE

### Milestone 3
- [ ] `seed_database()` runs in < 10 seconds
- [ ] Idempotent: second run returns same counts without duplicates
- [ ] All 100 patients, 20 staff, 8 rooms, 6 equipment, 14 days schedules present

### Milestone 4
- [ ] All 5 operations succeed on happy path
- [ ] Double-booking attempt raises `RoomConflictError`
- [ ] Cancelling an IN_PROGRESS appointment raises `AppointmentStateError`
- [ ] Emergency override creates Override row + bumps elective + audit entries

### Milestone 5
- [ ] All 5 notebooks execute `Run All` without errors
- [ ] Notebook 04 shows > 100 TPS for create_case
- [ ] Notebook 05 asserts `successes == 1` and `failures == 49`
- [ ] GIST safety net test produces `IntegrityError`
