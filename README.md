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
│   └── 05_isolation_test.ipynb
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

- Docker Desktop (for PostgreSQL)
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) package manager

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env — DATABASE_URL is pre-configured for the Docker container
```

### 2. Start the database

```bash
docker-compose up -d
docker-compose ps   # wait until status shows "healthy"
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

| Notebook | Assignment Requirement | What it demonstrates |
|---|---|---|
| `01_schema_and_orm.ipynb` | Req 1 & 2 — Schema & ORM | 16 SQLAlchemy model classes, `create_all()`, GIST constraint verification |
| `02_seed_data.ipynb` | Req 3 — Seeding | `seed_database()` with idempotency check |
| `03_atomic_operations.ipynb` | Req 4 — Atomic Operations | 5 operations end-to-end with full audit trail |
| `04_performance_test.ipynb` | Req 5 — Performance Testing | 10,000 create_case @ 612 TPS; concurrent booking with 20 threads |
| `05_isolation_test.ipynb` | Req 6 — Isolation Testing | 50 threads race for one room slot — exactly 1 succeeds |

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
| Throughput | **612 TPS** |
| P50 latency | 1.39 ms |
| P95 latency | 2.77 ms |
| P99 latency | 3.57 ms |

**Test 2 — concurrent `create_appointment()`, 500 attempts, 20 workers**

| Metric | Result |
|---|---|
| Successes | 84 |
| Room conflicts (`RoomConflictError`) | 0 |
| Staff conflicts (misclassified as "errors") | 416 |

> The 416 "errors" are `StaffNotAvailableError` — the test assigns the same surgeon and anaesthesiologist to all 500 slots, making simultaneous bookings across 6 rooms correctly impossible. The system is correct; only the test's outcome classification is misleading. See `TEST_REPORT.md §4` for a full explanation and fix.

---

## Concurrency Results

**NB05 — 50 threads racing for the same room and time slot**

| Check | Result |
|---|---|
| Successful bookings | **1** (exactly) |
| `RoomConflictError` raised | **49** (all others) |
| Unexpected errors | 0 |
| DB reservations after race | 1 |
| Total race duration | 275.7 ms |

`SELECT FOR UPDATE` serialised all 50 threads correctly — no double-booking occurred.

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

## Documentation

| File | Description |
|---|---|
| `TEST_REPORT.md` | Full test results for all 5 notebooks — pass/fail table, anomaly analysis, completeness vs assignment requirements |
| `TEST_TUTORIAL.md` | Per-notebook guide — configuration parameters, how to read the output, re-run safety notes, troubleshooting |
| `OR_Scheduling_Blueprint.md` | Authoritative system design — 16 tables, 7 operations, full ER diagram |
| `Assignment_Requirements.md` | Original 6 graded requirements from the assignment |

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

**"could not connect to server"**
```bash
docker-compose ps          # check container health
docker-compose up -d       # restart if stopped
docker-compose logs db     # inspect errors
```

**NB03/NB05 fails because of stale reservations from a prior run**

NB03 has an idempotent cleanup cell at the start — re-running it will clear all transactional data automatically. For NB05, change `ISOLATION_DATE` to a future date not previously used, or manually delete the conflicting reservation:
```sql
DELETE FROM room_reservations
WHERE room_id = (SELECT room_id FROM rooms WHERE room_code = 'OR-1')
  AND reservation_start::date = '<your ISOLATION_DATE>';
```
