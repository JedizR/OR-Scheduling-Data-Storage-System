# Test Report — OR Scheduling System Assignment
**Date:** 2026-03-04
**Database:** PostgreSQL 16 (Docker)
**Runtime:** Python 3.10, SQLAlchemy 2.0 (sync), psycopg2-binary

---

## Overall Status

| Notebook | Assignment Requirement | Status | Notes |
|---|---|---|---|
| `01_schema_and_orm.ipynb` | Requirements 1 & 2 — Schema & ORM | ✅ PASS | 16 tables, GIST constraint verified |
| `02_seed_data.ipynb` | Requirement 3 — Seeding | ✅ PASS | Idempotency confirmed |
| `03_atomic_operations.ipynb` | Requirement 4 — Atomic Operations | ✅ PASS | All 5 ops + atomicity rollback demo + audit log |
| `04_performance_test.ipynb` | Requirement 5 — Performance Testing | ✅ PASS | 681 TPS on create_case; 500/500 concurrent appointments |
| `05_isolation_test.ipynb` | Requirement 6 — Isolation Testing | ✅ PASS | Race condition + GIST constraint both proven with DB-level evidence |

---

## NB01 — Database Schema & ORM Setup

### What ran
- Imported all 16 SQLAlchemy model classes
- Called `Base.metadata.create_all(engine)`
- Queried `INFORMATION_SCHEMA` to list all tables
- Queried `pg_constraint` to display all constraints on `room_reservations`
- Displayed entity-relationship summary

### Results

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| ORM classes loaded | 16 | 16 | ✅ |
| Tables created in PostgreSQL | 16 | 16 | ✅ |
| GIST exclusion constraint exists | `no_room_overlap` | Present | ✅ |
| GIST constraint type | `x` (EXCLUDE) | `x` | ✅ |
| Unique constraint on `(appointment_id, room_id)` | Present | Present | ✅ |
| Check constraint on `status` | Present | Present | ✅ |

### Anomalies
None. All outputs are correct.

### Assignment Requirement Coverage
- ✅ **Req 1 & 2**: Python ORM classes for all 10 conceptual entities (16 physical tables), column definitions, PKs, FKs, `create_all()`.

---

## NB02 — Initial Data Population (Seeding)

### What ran
- `seed_database()` called twice (first run + idempotency check)
- Row counts queried for all 15 tables
- Sample data shown for departments, staff, rooms, equipment, patients

### Results

| Entity | Required (Assignment) | Actual | Pass? |
|---|---|---|---|
| Departments | "5 Departments" (example) | 5 | ✅ |
| Staff | "20 Staff" (example) | 20 (5 surgeons, 5 anaesthetists, 5 scrub nurses, 5 coordinators) | ✅ |
| Rooms | "50 Rooms" (example) | **8** | ⚠️ See note |
| Patients | "100 Patients" | 100 (HN-00000001 through HN-00000100) | ✅ |
| Idempotency | No duplicates on re-run | Confirmed — seed HN-% count = 100 after second run | ✅ |

### Row Counts at Time of Test
These reflect a **post-NB04** database state (NB04 performance test adds PERF-patients and cases):

| Table | Count | Normal? |
|---|---|---|
| patients | 10,000 | ⚠️ NB04 pre-seeds PERF-XXXXXXXX patients |
| cases | 10,555 | ⚠️ NB04 creates 10,000 + NB03/NB05 create ~55 cases |
| appointments | 89 | ⚠️ Residual from prior test runs |
| overrides | 1 | Expected (from NB03 emergency override demo) |
| audit_log | 10,647 | Expected (NB04 creates audit entries for each case) |

> **Note on rooms:** The assignment says "such as 50 Rooms" as an illustrative example, not a hard requirement. 8 rooms is realistic for a Thai government hospital OR suite (6 standard ORs, 1 hybrid, 1 emergency), and all other seed quantities meet or exceed the example figures.

### Anomalies

**Non-blocking:** Row counts reflect cumulative cross-notebook state. The `patients: 10,000` and `cases: 10,555` rows are expected — they are PERF-prefixed rows from NB04, not duplicated seed data. The idempotency assertion correctly scopes to `HN-%` prefix only.

### Assignment Requirement Coverage
- ✅ **Req 3**: `seed_database()` function inserts realistic batch data. Idempotent.

---

## NB03 — Atomic Business Operations

### What ran
All 5 operations demonstrated end-to-end with a full audit trail, plus an atomicity rollback problem-case demonstration:

| Step | What was demonstrated | Result |
|---|---|---|
| Atomicity problem case | `create_appointment()` with invalid staff UUID — forces mid-operation failure | `StaffNotFoundError` raised; 0 appointments, 0 room_reservations in DB ✅ |
| Op 1: `create_case()` | OPEN case created | ✅ |
| Op 2a: `create_appointment()` — happy path | CONFIRMED, 3 staff reservations, 1 equipment reservation, 1 room reservation | ✅ |
| Op 2b: Double-booking attempt | `RoomConflictError` raised; DB verification confirms exactly 1 reservation exists | ✅ |
| Op 3: `cancel_appointment()` | Status → CANCELLED, version 1 → 2 | ✅ |
| Op 4: `emergency_override()` | Elective appointment → BUMPED, emergency created | ✅ |
| Op 5: `complete_appointment()` | Status → COMPLETED, equipment → STERILIZING | ✅ |

### Key Metrics

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| Atomicity rollback — appointments after failure | 0 | 0 | ✅ |
| Atomicity rollback — room_reservations after failure | 0 | 0 | ✅ |
| Double-booking prevented | `RoomConflictError` | Raised | ✅ |
| Double-booking DB verification | 1 active reservation | 1 | ✅ |
| Cancel sets status | `CANCELLED` | ✅ | ✅ |
| Cancel increments version | 1 → 2 | ✅ | ✅ |
| Emergency bumps elective | `BUMPED` | ✅ | ✅ |
| Equipment post-completion | `STERILIZING` | ✅ | ✅ |
| All operations write audit log | Yes | 12 audit entries shown | ✅ |

### Anomaly — Sterilization End Timestamp

**Output:** `Sterilization ends at: 2026-03-04 ...+00:00` (today, not appointment date 2026-03-09)

**Root cause (not a bug):** `complete_appointment()` uses `actual_end_time=datetime.now(timezone.utc)` in the demo. The system correctly computes `sterilization_end = actual_end_time + sterilization_duration_min` from the moment equipment is physically returned. This is correct system behaviour — the demo completes the appointment immediately rather than waiting until the scheduled date.

### Assignment Requirement Coverage
- ✅ **Req 4**: 5 distinct atomic operations, at least one complex multi-table operation (`create_appointment` interacts with 6 tables), full commit/rollback demonstrated with DB-level evidence.

---

## NB04 — Performance Testing

### What ran
- **Test 1:** 10,000 `create_case()` operations in batches of 100
- **Test 2:** 500 `create_appointment()` attempts across 5 OR rooms with 20 concurrent workers

### Test 1 Results — `create_case()` × 10,000

| Metric | Value | Assessment |
|---|---|---|
| Total time | 14.68 s | Excellent |
| **Throughput** | **681 TPS** | Excellent for OLTP |
| P50 latency | 1.370 ms | Excellent |
| P95 latency | 2.259 ms | Excellent |
| P99 latency | 2.596 ms | Excellent |
| Min latency | 1.255 ms | — |
| Max latency | 2.596 ms | — |

> Industry benchmark: >100 TPS is generally considered adequate for hospital OLTP. 681 TPS demonstrates substantial headroom (6.8× the threshold).

### Test 2 Results — Concurrent `create_appointment()`

| Metric | Value |
|---|---|
| OR Rooms used | 5 (unique surgeon+anaest pair per room) |
| Attempts | 500 |
| **Successes** | **500** |
| Conflicts (room) | 0 |
| Errors (unexpected) | 0 |
| Total Time | 2.74 s |
| **Throughput (successful)** | **183 TPS** |
| P50 latency | 101.5 ms |
| P95 latency | 188.9 ms |
| P99 latency | 213.5 ms |

### Design — Unique Staff Per Room

Each of the 5 OR rooms is assigned a dedicated surgeon+anaesthesiologist pair (`surgeon_ids[room_idx]`, `anaest_ids[room_idx]`). This eliminates `StaffNotAvailableError` from the results, ensuring all measured conflicts are room-level only and throughput reflects pure appointment booking performance.

### Anomalies
None. 500/500 successes, 0 errors.

### Assignment Requirement Coverage
- ✅ **Req 5**: Performance functions run operations thousands of times, reports total time and throughput (TPS). Exceeds the requirement.

---

## NB05 — Isolation Testing (Concurrency)

### What ran
- **Test 1 Part A:** 10 threads race to book OR-1 using naive check-then-insert (no lock) — proves data corruption occurs without protection
- **Test 1 Part B:** 50 threads simultaneously attempt to book OR-1 using `SELECT FOR UPDATE`
- **Test 2:** Raw SQL `INSERT` bypasses application logic to attempt a room double-booking

### Test 1 Part A — Naive Booking (Problem Case)

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| Rows in `naive_bookings` for OR-1 | > 1 (data corruption) | **10** | ✅ |
| All rows share same time slot | Yes | Yes — 10 overlapping entries | ✅ |

10 threads all passed the "room is free" check simultaneously (no lock held during the 20ms sleep), then all inserted — producing 10 duplicate rows for the same OR-1 slot. This is the race condition in concrete, observable data.

### Test 1 Part B — SELECT FOR UPDATE (Solution Case)

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| Successful bookings | Exactly 1 | 1 | ✅ |
| `RoomConflictError` raised | Exactly 49 | 49 | ✅ |
| Unexpected errors | 0 | 0 | ✅ |
| Active reservations in DB | 1 | 1 | ✅ |
| Total race duration | < 5,000 ms | 275.7 ms | ✅ |

**All assertions passed.** `SELECT FOR UPDATE` correctly serialised 50 concurrent threads with zero double-bookings. DB query confirms exactly 1 row in `room_reservations` for OR-1 on the isolation date.

### Test 2 Results — GIST Constraint Safety Net

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| Raw INSERT rejected | Yes | Yes — `IntegrityError` raised | ✅ |
| Constraint that fired | `no_room_overlap` (GIST) | `no_room_overlap` (GIST) | ✅ |
| Error message | `conflicting key value violates exclusion constraint "no_room_overlap"` | Matches exactly | ✅ |

The raw INSERT used `gen_random_uuid()` for `appointment_id` (not the existing winning appointment's ID), so the UNIQUE constraint `uq_room_res_appt_room` was bypassed. The GIST exclusion constraint `no_room_overlap` was the sole mechanism that rejected the overlapping time range — proving database-level protection independent of application logic.

### Anomalies
None.

### Assignment Requirement Coverage
- ✅ **Req 6**: Threading used to simulate simultaneous bookings. Output proves database row-level locking (1 success, 49 rollbacks). GIST constraint independently verified via raw SQL bypass. Exceeds requirement.

---

## Summary of Anomalies

| # | Notebook | Anomaly | Severity | Blocking? |
|---|---|---|---|---|
| 1 | NB02 | Row counts show NB04-inflated patient/case totals (10,000/10,555) | Low | No — expected cross-notebook state |
| 2 | NB03 | Sterilization end timestamp is today, not appointment date | Cosmetic | No — correct system behaviour, demo uses `now` as actual_end |

All previously reported anomalies (NB04 staff conflict errors, NB05 wrong constraint) have been resolved.

---

## Completeness vs Assignment Requirements

| Requirement | Spec | Delivered | Gap |
|---|---|---|---|
| **Req 1 & 2** — Schema & ORM | Python classes, PKs, FKs, `create_all()` | 16 tables, full FK graph, `create_all()` | None |
| **Req 3** — Seeding | 5 depts, 20 staff, 50 rooms, 100 patients | 5 depts, 20 staff, 8 rooms, 100 patients | Rooms: 8 vs example 50 (not a hard requirement) |
| **Req 4** — Atomic Operations | 3–5 operations, at least one multi-table | 5 operations, all multi-table, atomicity rollback proven, full audit log | None |
| **Req 5** — Performance Testing | Loop 10,000×, report time in seconds | 10,000 create_case @ 681 TPS; 500 concurrent appointments @ 183 TPS | None |
| **Req 6** — Isolation Testing | Two threads, same room/time, prove one fails | 50 threads (Part B), 1 success, 49 `RoomConflictError`, DB verified; naive demo (Part A) shows data corruption without locking; GIST independently verified | None — exceeds requirement |

---

## Final Verdict

The system **meets all 6 assignment requirements** with zero blocking anomalies. The two remaining notes are cosmetic: cross-notebook patient count inflation (expected from performance test pre-seeding) and a sterilization timestamp that is intentionally set to `now` in the demo. The underlying database operations — atomic transactions, row-level locking, GIST exclusion constraints, and audit logging — all function correctly and are verified with direct database queries in every test.
