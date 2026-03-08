# OR Scheduling Data Storage System

A backend OLTP database system for coordinating Operating Room (OR) scheduling at a Thai government hospital. Built as an academic assignment demonstrating database schema design, atomic transactions, performance testing, and concurrency control using Python and PostgreSQL.

---

## Overview

The system manages the full lifecycle of surgical cases — from patient registration through room booking, staff and equipment allocation, emergency overrides, and post-surgery completion. It is backed by PostgreSQL with a 16-table normalised schema, GIST exclusion constraints for double-booking prevention, row-level locking for concurrent access safety, and a full audit trail on every state change.

**Technology stack:**

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| ORM | SQLAlchemy 2.0 (synchronous) |
| Database | PostgreSQL 16 (Docker) |
| Package manager | uv |
| Notebooks | Jupyter + ipykernel |
| Validation | pydantic-settings |
| Data generation | Faker |
| Output formatting | Rich |
| Document store | MongoDB 7 (Docker) |
| MongoDB driver | PyMongo 4.6 (synchronous) |
| Cache / queue | Redis 7 (Docker) |

---

## Project Structure

```
OR-Scheduling-Data-Storage-System/
├── src/or_scheduler/
│   ├── config.py               # Settings loaded from .env
│   ├── database.py             # Engine, SessionLocal, connection pool
│   ├── seed.py                 # seed_database() — idempotent reference data
│   ├── models/
│   │   ├── base.py             # DeclarativeBase
│   │   ├── department.py       # Department
│   │   ├── staff.py            # Staff
│   │   ├── room.py             # Room
│   │   ├── equipment.py        # Equipment
│   │   ├── patient.py          # Patient
│   │   ├── case.py             # Case (surgical work order)
│   │   ├── appointment.py      # Appointment (OR booking)
│   │   ├── reservation.py      # RoomReservation, StaffReservation, EquipmentReservation
│   │   ├── schedule.py         # RoomSchedule, StaffSchedule, EquipmentSchedule
│   │   └── override.py         # Override, OverrideDisplacedAppointment, AuditLog
│   └── operations/
│       ├── exceptions.py       # SchedulingError hierarchy (14 exception types)
│       ├── create_case.py      # Op 1: create_case()
│       ├── create_appointment.py # Op 2: create_appointment()
│       ├── cancel_appointment.py # Op 3: cancel_appointment()
│       ├── emergency_override.py # Op 4: emergency_override()
│       └── complete_appointment.py # Op 5: complete_appointment()
├── scripts/
│   └── init_db.py              # Creates all tables, GIST constraint, indexes, triggers
├── Assignment/
│   ├── 01_schema_and_orm.ipynb
│   ├── 02_seed_data.ipynb
│   ├── 03_atomic_operations.ipynb
│   ├── 04_performance_test.ipynb
│   ├── 05_isolation_test.ipynb
│   └── 06_mongodb_performance.ipynb  # Assignment 02
├── src/or_scheduler/
│   ├── mongo_client.py             # MongoClient singleton, indexes, WriteConcern
│   └── mongo_operations.py         # insert_or_events, update_or_events + tests
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── TEST_REPORT.md
└── TEST_TUTORIAL.md
```

---

## Database Schema

16 tables across 10 conceptual entities:

```
departments
    └── rooms ──────────────── room_schedules
    │                         └── room_reservations ─────┐
    └── staff ──────────────── staff_schedules           │
    │                         └── staff_reservations     ├── appointments ── cases ── patients
    └── equipment ──────────── equipment_schedules       │
                              └── equipment_reservations ┘
                                                      │
                         overrides ◄──────────────────┘
                         override_displaced_appointments
                         audit_log
```

### Key constraints

- **GIST exclusion constraint** (`no_room_overlap`) on `room_reservations`: prevents two active reservations for the same room with overlapping time ranges at the database level, independent of application logic.
- **Optimistic locking** via `version` column on `appointments`: detects concurrent modifications.
- **Row-level locking** (`SELECT FOR UPDATE`) in all booking operations: serialises concurrent transactions on the same room.
- **14 triggers** maintain `updated_at` timestamps and audit log entries automatically.

---

## Setup

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (runs PostgreSQL, MongoDB, and Redis)
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

### 1. Configure environment

```bash
cp .env.example .env
# .env.example is ready to use as-is — all three services are pre-configured:
#   DATABASE_URL=postgresql://orscheduler:orscheduler@localhost:5432/orscheduler
#   MONGODB_URI=mongodb://localhost:27017
#   REDIS_HOST=localhost  /  REDIS_PORT=6379
# Edit only if you need non-default ports.
```

### 2. Start all services

```bash
docker-compose up -d
docker-compose ps   # wait until all services show "healthy"
# Services: PostgreSQL 16 (port 5432), MongoDB 7 (port 27017), Redis 7 (port 6379)
```

### 3. Install dependencies

```bash
uv sync
uv sync --extra dev   # includes jupyter, ipykernel, nbconvert
```

### 4. Initialise the database schema

```bash
uv run python scripts/init_db.py
# Creates 16 tables, GIST exclusion constraint, 10 indexes, 14 triggers
```

### 5. Register the Jupyter kernel

```bash
uv run python -m ipykernel install --user --name or-scheduler --display-name "or-scheduler"
```

---

## Running the Assignment Notebooks

Run the notebooks **in order** — NB02 seeds reference data that all other notebooks depend on.

```
01 → 02 → 03 → 04 → 05
```

### Execute a single notebook (non-interactive)

```bash
uv run jupyter nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.timeout=600 \
  --ExecutePreprocessor.kernel_name=or-scheduler \
  Assignment/01_schema_and_orm.ipynb
```

### Open interactively

```bash
uv run jupyter lab
```

### Notebook summary

#### Assignment 01 — PostgreSQL OLTP

| Notebook | Assignment Requirement | What it demonstrates |
|---|---|---|
| `01_schema_and_orm.ipynb` | Req 1 & 2 — Schema & ORM | 16 SQLAlchemy model classes, `create_all()`, GIST constraint verification |
| `02_seed_data.ipynb` | Req 3 — Seeding | `seed_database()` with idempotency check |
| `03_atomic_operations.ipynb` | Req 4 — Atomic Operations | 5 operations end-to-end with full audit trail |
| `04_performance_test.ipynb` | Req 5 — Performance Testing | 10,000 create_case @ 681 TPS; 500 concurrent appointments @ 183 TPS, 500/500 successes |
| `05_isolation_test.ipynb` | Req 6 — Isolation Testing | Naive demo proves data corruption; 50 threads race with SELECT FOR UPDATE — exactly 1 succeeds; GIST constraint independently verified |

#### Assignment 02 — MongoDB High-RPS OLTP

Run NB06 independently — it connects to MongoDB, not PostgreSQL.

| Notebook | Assignment Requirement | What it demonstrates |
|---|---|---|
| `06_mongodb_performance.ipynb` | Support >10,000 RPS with NoSQL | `insert_or_events` + `test_insert_performance` (50k docs, naive 3,818 TPS → optimised **240,740 TPS**); `update_or_events` + `test_update_performance` (5k docs, naive 4,018 TPS → optimised **315,503 TPS**) |

---

## System Flow per Notebook

### NB01 — Schema & ORM Setup

```
Import 16 SQLAlchemy model classes
        │
        ▼
Base.metadata.create_all(engine)
  └─ Creates all 16 tables, GIST constraint, indexes, triggers
        │
        ▼
Query INFORMATION_SCHEMA.tables
  └─ Verify all 16 tables exist in PostgreSQL
        │
        ▼
Query pg_constraint WHERE conrelid = 'room_reservations'
  └─ Verify GIST exclusion constraint (no_room_overlap, contype='x') present
        │
        ▼
Display ORM column definitions per model (type, nullable, PK, FK)
```

### NB02 — Initial Data Population

```
seed_database()  ← First call
  ├─ INSERT 5 departments
  ├─ INSERT 8 rooms (OR-1..OR-6, Hybrid-OR, Emergency-OR)
  ├─ INSERT 6 equipment units
  ├─ INSERT 20 staff (5 surgeons, 5 anaests, 5 scrub nurses, 5 coords)
  ├─ INSERT 100 patients (HN-00000001..HN-00000100)
  └─ INSERT ~476 room + staff schedules (14-day window)
        │
        ▼
Query row counts for all tables → display summary
        │
        ▼
seed_database()  ← Second call (idempotency check)
  └─ All inserts use get-or-create → zero duplicates
        │
        ▼
SELECT COUNT(*) FROM patients WHERE hn LIKE 'HN-%'
  └─ Assert = 100  ✅
```

### NB03 — Atomic Business Operations

```
CLEANUP CELL (idempotent, runs every time)
  DELETE override_displaced_appointments → overrides → audit_log
  DELETE equipment_reservations → staff_reservations → room_reservations
  DELETE appointments → cases
        │
        ▼
Op 1: create_case(patient_hn, dept, surgeon, procedure)
  └─ INSERT cases → AuditLog[CREATED]
        │
        ▼
Op 2a: create_appointment(case, room=OR-3, 08:00–10:00, staff×3, equip×1)
  ├─ Lock room row (SELECT FOR UPDATE)
  ├─ Check RoomSchedule, StaffSchedule covers window
  ├─ INSERT appointment[CONFIRMED]
  ├─ INSERT room_reservation, staff_reservations×3, equipment_reservation
  └─ AuditLog[CONFIRMED]
        │
Op 2b: create_appointment(same room, same time)  →  RoomConflictError ✓
        │
        ▼
Op 3: cancel_appointment(appt_id)
  ├─ UPDATE appointment.status → CANCELLED, version 1→2
  ├─ UPDATE all reservations.status → RELEASED
  └─ AuditLog[CANCELLED]
        │
        ▼
Op 4: emergency_override(elective_appt, emergency_case, auth_code)
  ├─ UPDATE elective appointment.status → BUMPED
  ├─ INSERT override record + override_displaced_appointments
  ├─ INSERT emergency appointment[CONFIRMED] (same room/time as elective)
  └─ AuditLog[BUMPED, CONFIRMED]
        │
        ▼
Op 5: complete_appointment(appt_id, actual_end_time=now)
  ├─ UPDATE appointment.status → COMPLETED
  ├─ UPDATE equipment_reservation.status → STERILIZING
  │    sterilization_end = actual_end_time + sterilization_duration_min
  └─ AuditLog[COMPLETED]
        │
        ▼
Display last 20 audit_log entries
```

### NB04 — Performance Testing

```
SETUP
  Load dept, rooms (×6 OR), all surgeons (×5), all anaesthesiologists (×5)
        │
        ▼
TEST 1: create_case() × 10,000
  Pre-seed 10,000 PERF-XXXXXXXX patients (if not already present)
  Load patient HNs
  Loop 100 batches × 100 cases:
    SessionLocal → create_case() × 100 → commit
    Record batch latency
  Measure: total time, TPS, P50/P95/P99 latency
        │
        ▼
TEST 2: concurrent create_appointment() × 500
  IDEMPOTENCY CLEANUP: delete prior reservations for OR-1..OR-5, today+7..today+35
  Pre-create 500 fresh cases
  Build 500 booking slots:
    room_idx = slot_i % 5           → unique room per slot group
    surgeon = surgeon_ids[room_idx] → unique surgeon per room (no cross-room conflict)
    anaest  = anaest_ids[room_idx]  → unique anaesthesiologist per room
    time    = 08:00 + (slot_in_day × 2h), date = today + 7..N days
  Ensure RoomSchedule + StaffSchedule exist for all future dates
  ThreadPoolExecutor(20 workers):
    Each thread: create_appointment(slot) → commit → 'success'
                 RoomConflictError        → 'conflict'
                 Any other exception      → 'error'
  Measure: successes, conflicts, errors, TPS
```

### NB05 — Isolation Testing (Concurrency)

```
SETUP
  ISOLATION_DATE = today + 30 days  (fresh date)
  IDEMPOTENCY CLEANUP: delete prior reservations for OR-1 on ISOLATION_DATE
  Load OR-1, 1 surgeon, 1 anaesthesiologist
  Ensure RoomSchedule + StaffSchedule exist for ISOLATION_DATE
  Pre-create 50 cases (one per thread)
        │
        ▼
TEST 1 PART A: Naive Check-Then-Insert (Problem Case — No Locking)
  CREATE TABLE naive_bookings (no constraints — no GIST, no UNIQUE)
  threading.Barrier(10) → 10 threads start simultaneously
  Each thread:
    SELECT COUNT(*) FROM naive_bookings  ← room appears free (no lock)
    sleep(20ms)                          ← widen race window
    INSERT INTO naive_bookings           ← all succeed with no constraint to block them
  Result: 10 rows, all with identical booked_from/booked_until → DATA CORRUPTION ✅
  DROP TABLE naive_bookings (cleanup)
        │
        ▼
TEST 1 PART B: SELECT FOR UPDATE (Solution Case)
  threading.Barrier(50) → all 50 threads held until all are ready
  Release simultaneously:
    Each thread calls create_appointment(OR-1, 08:00–10:00)
      └─ SELECT room FOR UPDATE (PostgreSQL row lock)
           Thread-1: acquires lock → INSERT reservation → COMMIT → SUCCESS
           Threads 2–50: blocked until Thread-1 commits
                         → re-reads: reservation exists → RoomConflictError
  DB verification: SELECT actual row from room_reservations → exactly 1 row shown
  Assert: successes=1, conflicts=49, errors=0, db_reservations=1  ✅
        │
        ▼
TEST 2: GIST Constraint Safety Net
  Raw SQL INSERT (bypasses all application logic):
    appointment_id = gen_random_uuid()  ← fresh UUID bypasses UNIQUE constraint
    room_id        = OR-1 room_id       ← same room
    time range     = 08:00–10:00        ← overlaps existing reservation
  PostgreSQL evaluates GIST: tstzrange overlaps → IntegrityError:
    "conflicting key value violates exclusion constraint no_room_overlap"  ✅
        │
        ▼
Summary: two independent layers proven:
  Layer 1 (Application) → SELECT FOR UPDATE serializes concurrent transactions
  Layer 2 (Database)    → GIST exclusion constraint rejects invalid INSERTs
```

---

## Atomic Operations

All five operations are in `src/or_scheduler/operations/` and use explicit transactions with commit/rollback.

| Operation | Function | Tables touched |
|---|---|---|
| Create surgical case | `create_case(patient_hn, procedure_name, ...)` | `cases`, `audit_log` |
| Book OR slot | `create_appointment(case_id, room_code, start, end, staff_ids, equipment_ids)` | `appointments`, `room_reservations`, `staff_reservations`, `equipment_reservations`, `audit_log`, `schedules` |
| Cancel booking | `cancel_appointment(appointment_id)` | `appointments`, `room_reservations`, `staff_reservations`, `equipment_reservations`, `audit_log` |
| Emergency override | `emergency_override(elective_appt_id, emergency_case_id, auth_code, ...)` | `appointments` (×2), `overrides`, `override_displaced_appointments`, `room_reservations`, `audit_log` |
| Complete surgery | `complete_appointment(appointment_id, actual_end_time)` | `appointments`, `equipment_reservations`, `audit_log` |

### Exception hierarchy

```
SchedulingError
├── PatientNotFoundError
├── CaseNotFoundError
├── AppointmentNotFoundError
├── StaffNotFoundError / StaffNotActiveError / StaffNotAvailableError
├── RoomNotFoundError / RoomNotActiveError / RoomNotScheduledError / RoomConflictError
├── EquipmentNotFoundError / EquipmentNotAvailableError
├── AppointmentStateError
├── OptimisticLockError
└── AuthorizationError
```

---

## Performance Results

Tested on PostgreSQL 16 (Docker), Python 3.10, macOS.

**Test 1 — `create_case()` × 10,000**

| Metric | Result |
|---|---|
| Throughput | **681 TPS** |
| P50 latency | 1.370 ms |
| P95 latency | 2.259 ms |
| P99 latency | 2.596 ms |

**Test 2 — concurrent `create_appointment()`, 500 attempts, 20 workers, 5 OR rooms**

| Metric | Result |
|---|---|
| Successes | **500** |
| Conflicts (`RoomConflictError`) | 0 |
| Errors (unexpected) | 0 |
| Throughput (successful) | **183 TPS** |
| P50 latency | 101.5 ms |
| P95 latency | 188.9 ms |

Each room is assigned a unique surgeon+anaesthesiologist pair, eliminating staff conflicts so all 500 slots succeed.

---

## Concurrency Results

**NB05 Test 1A — Naive check-then-insert (no locking): data corruption proven**

| Check | Result |
|---|---|
| Threads that inserted | 10 |
| Rows in `naive_bookings` for same slot | **10** (all identical time range) |
| Data corruption | ✅ Confirmed — race condition is real |

**NB05 Test 1B — SELECT FOR UPDATE: 50 threads racing for one slot**

| Check | Result |
|---|---|
| Successful bookings | **1** (exactly) |
| `RoomConflictError` raised | **49** (all others) |
| Unexpected errors | 0 |
| DB reservations after race | 1 |
| Total race duration | 275.7 ms |

**NB05 Test 2 — GIST constraint: raw SQL bypass rejected**

| Check | Result |
|---|---|
| Constraint fired | `no_room_overlap` (GIST exclusion) |
| Error | `conflicting key value violates exclusion constraint "no_room_overlap"` |

Two independent protection layers verified: `SELECT FOR UPDATE` (application) and GIST exclusion (database).

---

## Seed Data

`seed_database()` in `src/or_scheduler/seed.py` is idempotent — safe to run multiple times.

| Entity | Count |
|---|---|
| Departments | 5 |
| Rooms | 8 (OR-1 through OR-6, Hybrid-OR, Emergency-OR) |
| Staff | 20 (5 surgeons, 5 anaesthesiologists, 5 scrub nurses, 5 coordinators) |
| Equipment | 6 units |
| Patients | 100 (HN-00000001 through HN-00000100) |
| Room schedules | ~476 entries (14 days × 8 rooms × shift windows) |

---

## MongoDB High-RPS Results (Assignment 02)

Tested on MongoDB 7.0.30 (Docker), PyMongo 4.16.0, Python 3.10.18, Apple M-series.

| Test | Approach | Documents | TPS | Requirement |
|------|----------|-----------|-----|-------------|
| Insert | Naive (`insert_one` × N) | 50,000 | 3,818 | — |
| Insert | Optimised | 50,000 | **240,740** | ✅ >10,000 |
| Update | Naive (`update_one` × N) | 5,000 | 4,018 | — |
| Update | Optimised | 5,000 | **315,503** | ✅ >10,000 |

Insert optimised is **63× faster** than naive. Update optimised is **79× faster** than naive.

See `Documents/Tests/TEST02_REPORT.md` for full details and `Documents/Tests/ASSIGNMENT02_TECHNIQUES.md` for technique explanations with code references.

---

## Documentation

| File | Description |
|---|---|
| `Documents/Tests/TEST_REPORT.md` | Full test results for NB01–NB05 — pass/fail table, anomaly analysis |
| `Documents/Tests/TEST02_REPORT.md` | Full test results for NB06 (MongoDB) — TPS results, technique breakdown |
| `Documents/Tests/ASSIGNMENT02_TECHNIQUES.md` | Every optimisation technique explained with code file references |
| `Documents/Tests/TEST_TUTORIAL.md` | Per-notebook guide — configuration, re-run safety, troubleshooting |
| `Documents/Contexts/OR_Scheduling_Blueprint.md` | Authoritative system design — 16 tables, 7 operations, full ER diagram |
| `Documents/Tests/Assignment_Requirements.md` | Original graded requirements |

---

## Troubleshooting

**"kernel not found: or-scheduler"**
```bash
uv run python -m ipykernel install --user --name or-scheduler --display-name "or-scheduler"
```

**"ValidationError: database_url field required"**
```bash
cat .env   # verify DATABASE_URL exists
# Run notebooks from project root, not from Assignment/
cd /path/to/OR-Scheduling-Data-Storage-System
uv run jupyter nbconvert ...
```

**"could not connect to server" (PostgreSQL)**
```bash
docker-compose ps          # check container health
docker-compose up -d       # restart if stopped
docker-compose logs db     # inspect errors
```

**"ServerSelectionTimeoutError" or "Connection refused" (MongoDB — NB06)**
```bash
docker-compose ps mongo            # confirm mongo container is running
docker-compose up -d mongo         # start if stopped
docker-compose logs mongo          # inspect errors
# Verify: docker exec -it <container> mongosh --eval "db.adminCommand('ping')"
```

**"redis.exceptions.ConnectionError" (Redis — NB06 write-buffer test)**
```bash
docker-compose ps redis            # confirm redis container is running
docker-compose up -d redis         # start if stopped
docker-compose logs redis          # inspect errors
```

**NB06 runs without PostgreSQL**

`06_mongodb_performance.ipynb` connects to MongoDB only. You do **not** need to run `init_db.py` or any NB01–NB05 step before running NB06.

**NB03/NB04/NB05 fail because of stale reservations from a prior run**

All three notebooks are fully idempotent — each has a cleanup step that deletes prior test reservations before running. Simply re-run the notebook from the top:
- **NB03**: cleanup cell at the start deletes all transactional data (cases, appointments, reservations, audit_log)
- **NB04**: `test_concurrent_booking_performance()` clears prior reservations for OR-1..OR-5 on `today+7..today+35` before booking
- **NB05**: setup cell clears prior reservations for OR-1 on `ISOLATION_DATE` before creating test cases
