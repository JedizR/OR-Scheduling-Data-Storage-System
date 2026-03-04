# OR Scheduling System — Project Roadmap

> **Target:** Thai government hospital single-site OR coordination platform
> **Language:** Python 3.10+ | **Database:** PostgreSQL 16 | **Stack:** SQLAlchemy 2.0 · FastAPI · Redis · DuckDB

---

## Priority Order

Milestones 1–5 must be completed first — they constitute the **assignment deliverables**.
Milestones 6–9 implement the full production system from the blueprint.
Each milestone is designed to be independently runnable and verifiable.

---

## Milestone 1 — Project Infrastructure
**Goal:** Runnable PostgreSQL environment, Python project wired up, health confirmed.

- [ ] `pyproject.toml` — uv project with all dependencies pinned
- [ ] `docker-compose.yml` — PostgreSQL 16 service (port 5432, persistent volume)
- [ ] `.env.example` + `.env` — DATABASE_URL and configurable settings
- [ ] `src/or_scheduler/config.py` — pydantic-settings configuration class
- [ ] `src/or_scheduler/database.py` — synchronous engine, session factory, `get_session()`
- [ ] `scripts/init_db.py` — create extensions (`uuid-ossp`, `btree_gist`), create all tables
- [ ] Verify: `python scripts/init_db.py` succeeds against running Docker container

**Standalone test:** `python scripts/init_db.py` → tables exist, extensions loaded.

---

## Milestone 2 — ORM Models (Database Schema)
**Goal:** All 16 tables defined as SQLAlchemy ORM classes with full constraints.

- [ ] `src/or_scheduler/models/base.py` — `Base`, `TimestampMixin` (created_at / updated_at)
- [ ] `departments` — Department model
- [ ] `staff` — Staff model with role CHECK constraint
- [ ] `rooms` — Room model with room_type CHECK constraint
- [ ] `equipment` — Equipment model with status CHECK constraint
- [ ] `patients` — Patient model with HN uniqueness
- [ ] `cases` — Case model with urgency + status CHECK constraints
- [ ] `appointments` — Appointment model with version column (optimistic lock)
- [ ] `room_reservations` — with `reservation_start`/`reservation_end` TIMESTAMPTZ + GIST exclusion constraint
- [ ] `staff_reservations` — with time range columns
- [ ] `equipment_reservations` — with time range columns
- [ ] `room_schedules` / `staff_schedules` / `equipment_schedules` — Schedule models
- [ ] `overrides` + `override_displaced_appointments` — Override + junction
- [ ] `audit_log` — BIGSERIAL PK, append-only (no update/delete triggers)
- [ ] All indexes from blueprint Part 6.4
- [ ] `updated_at` auto-update trigger on all mutable tables

**Standalone test:** `scripts/init_db.py` → inspect tables in psql, all 16 present with constraints.

---

## Milestone 3 — Seed Data
**Goal:** Realistic starting dataset for all demonstrations and tests.

- [ ] `src/or_scheduler/seed.py` — `seed_database()` function
- [ ] Seed quantities:
  - 5 Departments (Orthopaedics, Cardiac, Neurosurgery, General Surgery, Anaesthesiology)
  - 20 Staff (5 surgeons, 5 anaesthesiologists, 5 scrub nurses, 5 coordinators)
  - 8 Rooms (OR-1 through OR-6 + HYBRID-1 + ER-1)
  - 6 Equipment units (2× C-arm, 2× Laparoscopic Tower, 1× da Vinci robot, 1× Cell Saver)
  - 100 Patients (realistic Thai names + HN numbers)
  - 14 days of schedules for all rooms, staff, and equipment (08:00–17:00 REGULAR)
- [ ] Idempotent: re-running seed does not duplicate data
- [ ] Returns summary dict: `{"departments": 5, "staff": 20, ...}`

**Standalone test:** Run seed → query `SELECT COUNT(*) FROM patients` → 100.

---

## Milestone 4 — Atomic Business Operations
**Goal:** 5 production-grade transactional functions, fully locking, fully rolling back on failure.

- [ ] `src/or_scheduler/operations/base.py` — Exception hierarchy: `SchedulingError`, `RoomConflictError`, `StaffNotAvailableError`, `EquipmentNotAvailableError`, `AppointmentStateError`
- [ ] **Operation 1 — `create_case()`** — Patient lookup by HN, Case INSERT, audit log. No locking.
- [ ] **Operation 2 — `create_appointment()`** — THE CORE OPERATION. Full locking: Room → Equipment (ASC) → Staff (ASC). Schedule check + overlap check. Atomic insert of Appointment + all Reservations.
- [ ] **Operation 3 — `cancel_appointment()`** — Lock appointment, release all 3 resource types atomically, status → CANCELLED.
- [ ] **Operation 4 — `emergency_override()`** — NOWAIT lock, bump 1..N elective appointments, insert Override + junction. Full audit trail.
- [ ] **Operation 5 — `complete_appointment()`** — Status → COMPLETED, reservation statuses → COMPLETED, equipment → STERILIZING.
- [ ] Each operation writes to `audit_log` with `pg_current_xact_id()`
- [ ] Each operation returns a typed result dataclass

**Standalone test:** Python script exercising each operation against seeded data.

---

## Milestone 5 — Assignment Deliverables (Notebooks)
**Goal:** Five Jupyter notebooks that satisfy every graded requirement in Assignment_Requirements.md.

- [ ] `Assignment/01_schema_and_orm.ipynb`
  - Display all model classes with column definitions
  - Show FK relationships diagram (textual)
  - Execute `create_all` and verify with `INFORMATION_SCHEMA` queries
- [ ] `Assignment/02_seed_data.ipynb`
  - Run `seed_database()`, display counts per table
  - Sample queries showing realistic data
- [ ] `Assignment/03_atomic_operations.ipynb`
  - Demonstrate all 5 operations with narrative
  - Show commit/rollback behavior with intentional failures
  - Print audit_log entries after each operation
- [ ] `Assignment/04_performance_test.ipynb`
  - `test_create_case_performance()`: 10,000 inserts, measure wall-clock time + TPS
  - `test_create_appointment_performance()`: 2,000 concurrent bookings across 8 rooms, measure P50/P95/P99
  - Results table printed with `rich`
- [ ] `Assignment/05_isolation_test.ipynb`
  - `test_no_double_booking()`: 50 threads, all attempt to book OR-1 same slot simultaneously
  - Barrier synchronization to maximize concurrency
  - Assert: exactly 1 success, 49 `RoomConflictError` (or GIST violation caught)
  - Timeline printout proving serialization via SELECT FOR UPDATE
  - GIST bypass test: raw SQL INSERT attempt that violates constraint → IntegrityError

**Standalone test:** `jupyter nbconvert --to notebook --execute Assignment/*.ipynb` runs without error.

---

## Milestone 6 — FastAPI REST Layer
**Goal:** Production API matching blueprint Part 11 specification.

- [ ] `src/or_scheduler/api/` — FastAPI app with async SQLAlchemy (asyncpg)
- [ ] Pydantic v2 request/response schemas
- [ ] All endpoints: Cases, Appointments, Overrides, Availability, Schedules
- [ ] Role-based access control (coordinator / dept_head / admin)
- [ ] Standardized error responses (RFC 7807 Problem Details)
- [ ] `GET /health` endpoint
- [ ] OpenAPI docs auto-generated

---

## Milestone 7 — Redis + WebSocket Real-Time Layer
**Goal:** Live OR status board + tentative hold system.

- [ ] Redis service added to docker-compose.yml
- [ ] `src/or_scheduler/redis_client.py` — async redis-py client
- [ ] Lua script for atomic multi-resource tentative holds (90s TTL)
- [ ] `POST /appointments/hold` + `DELETE /appointments/hold/{session_id}`
- [ ] Post-commit Redis publish in all Operations 1–5
- [ ] WebSocket `/ws/status-board` — pushes room state changes to all connected clients
- [ ] Graceful degradation: Redis down → PostgreSQL-only mode, correctness preserved

---

## Milestone 8 — DuckDB Analytics Layer
**Goal:** OLAP analytics separate from OLTP, no impact on booking performance.

- [ ] DuckDB instance + star schema (fact_appointments, dim_date, dim_room, dim_staff, dim_department, dim_equipment)
- [ ] Nightly ETL: PostgreSQL → DuckDB (incremental by updated_at watermark)
- [ ] 4 analytical queries: OR utilization, equipment bottlenecks, cancellation trends, surgeon overtime
- [ ] Analytics API endpoints (`/analytics/*`)

---

## Milestone 9 — Production Hardening
**Goal:** Ready for staging deployment.

- [ ] Alembic migrations replacing `create_all`
- [ ] Connection pool tuning (pgBouncer or SQLAlchemy pool config)
- [ ] Structured JSON logging (structlog)
- [ ] Prometheus metrics endpoint
- [ ] Environment-specific configs (dev / staging / prod)
- [ ] GitHub Actions CI: lint (ruff) + type check (mypy) + test suite

---

## Technology Stack Summary

| Layer | Technology | Version | Used From |
|-------|-----------|---------|-----------|
| Database | PostgreSQL | 16+ | Milestone 1 |
| ORM | SQLAlchemy | 2.0 (sync) | Milestone 2 |
| Driver | psycopg2-binary | latest | Milestone 1 |
| Settings | pydantic-settings | 2.x | Milestone 1 |
| Seed/Fake Data | Faker | latest | Milestone 3 |
| Notebooks | Jupyter / ipykernel | latest | Milestone 5 |
| Pretty Output | rich | latest | Milestone 5 |
| API | FastAPI | 0.100+ | Milestone 6 |
| Async Driver | asyncpg | latest | Milestone 6 |
| Cache/Pub-Sub | Redis (redis-py) | 7+ | Milestone 7 |
| Analytics | DuckDB | 0.10+ | Milestone 8 |

---

## Assignment Completion Map

| Requirement | Covered By |
|------------|-----------|
| 1. Database Schema & ORM | Milestone 2 + Notebook 01 |
| 2. ORM Setup (create tables) | Milestone 1 + Milestone 2 + Notebook 01 |
| 3. Initial Data Population | Milestone 3 + Notebook 02 |
| 4. 3–5 Atomic Operations | Milestone 4 + Notebook 03 |
| 5. Performance Testing | Milestone 5 + Notebook 04 |
| 6. Isolation Testing | Milestone 5 + Notebook 05 |
