# Test Tutorial — OR Scheduling System Assignment Notebooks

This document explains how each test notebook works, what parameters you can adjust,
and how to interpret the results. All notebooks live in the `Assignment/` folder.

---

## Prerequisites

### 1. Start the Database
```bash
# From project root
docker-compose up -d

# Verify it's healthy
docker-compose ps        # should show "healthy"
```

### 2. Activate the Virtual Environment
```bash
source .venv/bin/activate
# or, using uv:
uv run python --version
```

### 3. Initialise the Database (first time only)
```bash
uv run python scripts/init_db.py
# Creates 16 tables, GIST constraint, 10 indexes, 14 triggers
```

### 4. Run a Notebook
```bash
# Execute and save output in-place
uv run jupyter nbconvert --to notebook --execute --inplace \
  --ExecutePreprocessor.timeout=600 \
  --ExecutePreprocessor.kernel_name=or-scheduler \
  Assignment/<notebook_name>.ipynb

# Or open interactively
uv run jupyter lab
```

---

## Recommended Execution Order

Run the notebooks in this order to avoid cross-notebook state issues:

```
01 → 02 → 03 → 04 → 05
```

> **NB02** seeds reference data (staff, rooms, equipment, patients) that all other notebooks depend on.
> **NB03** cleans its own transactional data at startup — safe to re-run anytime.

---

## NB01 — `01_schema_and_orm.ipynb`
**Assignment:** Requirements 1 & 2 (Schema & ORM)

### What it tests
- ORM model definitions (16 SQLAlchemy classes)
- `Base.metadata.create_all()` — creates tables in PostgreSQL
- `INFORMATION_SCHEMA` query to verify all tables exist
- `pg_constraint` query to verify the GIST exclusion constraint

### Configuration points

| Variable / Code | Location | What to change |
|---|---|---|
| `key_models` list | Cell 3 | Add/remove models to show their column definitions |
| `engine` import | Cell 4 | Connection is inherited from `or_scheduler.config` / `.env` |

### How to read the output

**ORM column table** — one table per model:
- `Column`: SQLAlchemy attribute name (= DB column name)
- `Type`: SQLAlchemy column type
- `Nullable`: `True` = optional, `False` = required
- `PK`: ✓ = part of primary key
- `FK`: shows `referenced_table.column` if this is a foreign key

**GIST constraint table** — look for `contype = x` and name `no_room_overlap`:
```
EXCLUDE USING gist (room_id WITH =,
  tstzrange(reservation_start, reservation_end, '[)') WITH &&)
WHERE (status NOT IN ('RELEASED', 'COMPLETED'))
```
This means: two reservations for the **same room** with **overlapping time ranges** are forbidden (unless at least one is RELEASED or COMPLETED).

### Expected final output
```
✅ Schema & ORM demonstration complete.
   16 tables defined and verified in PostgreSQL.
   GIST exclusion constraint confirms database-level double-booking prevention.
```

---

## NB02 — `02_seed_data.ipynb`
**Assignment:** Requirement 3 (Initial Data Population)

### What it tests
- `seed_database()` inserts realistic starting data
- Row counts for every table
- Sample data for each entity type
- Idempotency: running the seed twice does not create duplicates

### Configuration points

| Parameter | File | Default | How to change |
|---|---|---|---|
| Department count | `src/or_scheduler/seed.py` | 5 | Edit the `DEPARTMENTS` list |
| Room count | `src/or_scheduler/seed.py` | 8 | Edit the `ROOMS` list |
| Staff count | `src/or_scheduler/seed.py` | 20 | Edit `_get_or_create_staff()` |
| Patient count | `src/or_scheduler/seed.py` | 100 | Change `count=100` in `_get_or_create_patients()` call |
| Schedule days ahead | `src/or_scheduler/seed.py` | 14 | Change `days=14` in `_create_schedules()` call |
| Equipment units | `src/or_scheduler/seed.py` | 6 | Edit the `EQUIPMENT_DATA` list |

### How to read the output

**Row Counts table** — shows current DB state, NOT just what seed created:
- After NB04 runs, `patients` will show ~10,000 and `cases` ~10,555. This is expected — NB04 pre-seeds PERF patients for its load test. The idempotency assertion only checks `HN-%` pattern patients (the 100 seed patients).
- `schedules_created: 0` is normal on a re-run — all schedules already exist.

**Idempotency check** — the assertion specifically checks:
```sql
SELECT COUNT(*) FROM patients WHERE hn LIKE 'HN-%'
```
This counts only the seed-created patients (format `HN-00000001`), ignoring performance-test patients (`PERF-00000001`). It should always equal 100.

### Expected final output
```
✅ Idempotency confirmed — no duplicates created.
```

### What to do if it fails
```
AssertionError: Idempotency broken — duplicate HN seed patients detected!
```
This means the seed patient check returned more than 100. Check whether the seed's HN numbering scheme (`HN-00000001` through `HN-00000100`) collided with manually inserted data.

---

## NB03 — `03_atomic_operations.ipynb`
**Assignment:** Requirement 4 (Atomic Business Operations)

### What it tests
Five operations, each demonstrated with both the happy path and intentional failure:

| Operation | What it does |
|---|---|
| `create_case()` | Creates a surgical work order for a patient |
| `create_appointment()` | Books an OR slot — acquires room, staff, equipment locks atomically |
| `cancel_appointment()` | Releases all resources atomically, status → CANCELLED |
| `emergency_override()` | Bumps an elective appointment, creates emergency booking |
| `complete_appointment()` | Marks surgery done, equipment → STERILIZING |

### Configuration points

| Variable | Cell | Default | What it changes |
|---|---|---|---|
| `TARGET_DATE` | Cleanup cell | `today + 5 days` | Which date to demo on — must be within 14-day schedule window |
| `room_or1` | Setup cell | `OR-3` | Room used for Op 2 (booking demo) |
| `room_or2` | Setup cell | `OR-4` | Room used for Op 4 (emergency override demo) |
| Op 2 time slot | Cell 4 | `08:00–10:00` | When the appointment is booked |
| Op 4 elective time | Cell 6 | `10:30–12:00` | Must not overlap with Op 2's slot or any other booking |
| Op 4 emergency time | Cell 6 | `10:30–12:00` | Same slot as elective — emergency preempts it |
| `authorization_code` | Cell 6 | `'EMR-2026-001'` | Override reference number |

### Time slot rules
- Op 2 books `surgeon + anaest + scrub` at `08:00–10:00`
- Op 3 books `surgeon + anaest` at `11:00–12:30`, then cancels (resources freed)
- Op 4 elective must use a **non-overlapping** time slot (currently `10:30–12:00`) to avoid staff conflicts with Op 2
- All slots must fall within the schedule window (`08:00–17:00`)

### Idempotency
The notebook opens with a **cleanup cell** that deletes all transactional data before the demo:
```python
DELETE FROM override_displaced_appointments
DELETE FROM overrides
DELETE FROM audit_log
DELETE FROM equipment_reservations
DELETE FROM staff_reservations
DELETE FROM room_reservations
DELETE FROM appointments
DELETE FROM cases
```
This makes the notebook **safe to re-run as many times as needed**. Reference data (staff, rooms, equipment, patients, schedules) is preserved.

### How to read the output

**Operation tables** — each shows key fields of the created object:
- `version` on appointments starts at 1, increments on each state change (cancel = 2, etc.)
- `bumped_count` in override = number of appointments displaced

**Audit log** — final cell shows last 20 entries. Each row is one state transition:
- `entity_type`: which table changed (`CASE`, `APPOINTMENT`, `OVERRIDE`)
- `action`: what happened (`CREATED`, `CONFIRMED`, `CANCELLED`, `BUMPED`, `COMPLETED`)
- `old_status → new_status`: the before/after states

### Expected final output
```
✅ All 5 atomic operations demonstrated successfully.
   Every operation writes to audit_log with PostgreSQL transaction ID.
```

---

## NB04 — `04_performance_test.ipynb`
**Assignment:** Requirement 5 (Performance / Load Testing)

### What it tests
- **Test 1:** Throughput of `create_case()` — target is thousands of operations per second
- **Test 2:** Concurrent `create_appointment()` using `ThreadPoolExecutor`

### Configuration points

#### Test 1 — `test_create_case_performance(n)`

| Parameter | Default | Effect |
|---|---|---|
| `n` | `10_000` | Total cases to create |
| `BATCH_SIZE` | `100` | Cases per database transaction — larger = higher TPS but more data lost on failure |

```python
# To run a smaller test quickly:
result1 = test_create_case_performance(1_000)   # ~1.5 seconds

# To run the full assignment test:
result1 = test_create_case_performance(10_000)  # ~16 seconds
```

#### Test 2 — `test_concurrent_booking_performance(n_bookings, max_workers)`

| Parameter | Default | Effect |
|---|---|---|
| `n_bookings` | `500` | Total booking attempts |
| `max_workers` | `20` | ThreadPoolExecutor worker threads — must be ≤ connection pool size (`POOL_SIZE + MAX_OVERFLOW = 30`) |
| `day_offset` base | `7` | How many days in the future to place bookings (must have room schedules) |

```python
# To increase concurrency (must have POOL_SIZE >= n_workers in .env):
result2 = test_concurrent_booking_performance(500, max_workers=30)

# To measure only sequential bookings:
result2 = test_concurrent_booking_performance(100, max_workers=1)
```

### How to read the output

**Test 1 table:**
- `Throughput (TPS)` = operations per second across the whole run
- `P50 / P95 / P99 Latency` = per-operation latency at each percentile (ms per case, not ms per batch)
- Good target: P50 < 5 ms, TPS > 200

**Test 2 table:**
- `Successes` = appointments actually committed
- `Conflicts` = `RoomConflictError` — room already taken (correct behaviour)
- `Errors` = other exceptions — **currently shows ~416**

### Understanding the 416 "Errors" in Test 2

This is a **known test design limitation**, not a system bug.

**Why it happens:** The test assigns the same `surgeon_id` and `anaest_id` to all 500 booking slots. When slots in different rooms share the same time window (e.g., OR-1 through OR-6 all want 08:00–10:00 on day 7), only the first booking can acquire the staff members. Subsequent slots raise `StaffNotAvailableError`.

The `book_one()` function only catches `RoomConflictError`; staff conflicts fall into the `except Exception` branch and are counted as "errors".

**The core system is correct** — the staff double-booking was properly prevented. Only the test categorisation is misleading.

**To fix the test** (reduce errors to ~0), assign different staff members per room. With 5 surgeons and 5 anaesthesiologists available, up to 5 rooms can run simultaneously:

```python
# In test_concurrent_booking_performance():
staff_per_room = [
    (surgeon_ids[i % len(surgeon_ids)], anaest_ids[i % len(anaest_ids)])
    for i in range(len(OR_rooms))
]
```

### Connection pool tuning
Test 2 uses 20 threads each holding a connection. If you increase `max_workers`, also increase `.env` settings:

```ini
# .env
POOL_SIZE=20      # Base pool
MAX_OVERFLOW=10   # Extra connections allowed = max_workers up to 30
```

---

## NB05 — `05_isolation_test.ipynb`
**Assignment:** Requirement 6 (Isolation / Concurrency Testing)

### What it tests
- **Test 1:** 50 threads race to book OR-1 at the same time slot — exactly 1 must succeed
- **Test 2:** Raw SQL INSERT attempts to bypass the application and double-book a room at the database level

### Configuration points

| Variable | Default | Effect |
|---|---|---|
| `ISOLATION_DATE` | `today + 30 days` | Test date — far future avoids collision with NB03 (today+5) and NB04 (today+7 to +28) |
| `SLOT_START / SLOT_END` | `08:00–10:00` | The contested time slot |
| `NUM_THREADS` | `50` | Concurrent thread count — increasing creates more load on the locking mechanism |

```python
# To run a lighter test:
NUM_THREADS = 10  # still proves the concept, faster

# To stress-test the locking:
NUM_THREADS = 100  # increase POOL_SIZE in .env to at least 100
```

> **Important:** `ISOLATION_DATE` must be at least 1 day beyond the furthest NB04 booking date. NB04 books up to `today + 28 days`. Setting `ISOLATION_DATE = today + 30 days` gives a safe gap.

### How to read the output

**Thread results table** — sorted by elapsed time:
- One row per thread, sorted fastest → slowest
- The winning thread (SUCCESS, usually fastest) appears at the top
- All others show CONFLICT with the `RoomConflictError` message
- Elapsed time shows how long each thread waited for the lock before getting its answer

**Database verification:**
```
Active reservations for OR-1 on 2026-04-03: 1
```
This confirms that despite 50 concurrent attempts, only one reservation exists in the database.

**The 4 assertions:**
```python
assert len(successes) == 1        # exactly one winner
assert len(conflicts) == 49       # all others get RoomConflictError
assert len(errors) == 0           # no unexpected exceptions
assert active_reservations == 1   # DB confirms only 1 reservation
```

### Note on Test 2 — GIST Constraint

Test 2 attempts a raw SQL `INSERT` to prove the database has a safety net independent of application logic. **Current output:**
```
duplicate key value violates unique constraint "uq_room_res_appt_room"
```

This is the `UNIQUE (appointment_id, room_id)` constraint, not the GIST constraint. The insert is rejected, which is correct, but for the wrong reason — it reuses the same `appointment_id`.

**To properly test the GIST constraint**, the INSERT should use a fresh `appointment_id` with an overlapping time range:

```python
# Replace 'appt_id': winning_appt_id with a fresh UUID:
conn.execute(text("""
    INSERT INTO room_reservations
        (reservation_id, appointment_id, room_id, status,
         reservation_start, reservation_end, locked_at, created_at, updated_at)
    VALUES
        (gen_random_uuid(), gen_random_uuid(), :room_id, 'CONFIRMED',
         :res_start, :res_end, NOW(), NOW(), NOW())
"""), {
    'room_id': str(room_id),
    'res_start': datetime.combine(ISOLATION_DATE, SLOT_START, tzinfo=timezone.utc),
    'res_end':   datetime.combine(ISOLATION_DATE, SLOT_END,   tzinfo=timezone.utc),
})
```

With a new `appointment_id`, the UNIQUE constraint will pass, and the GIST exclusion constraint will fire and raise:
```
conflicting key value violates exclusion constraint "no_room_overlap"
```

### Re-running NB05
Unlike NB03, NB05 has **no cleanup cell**. On a second run on the same day:
- OR-1 on `ISOLATION_DATE` is already booked (from the first run)
- All 50 threads will get `RoomConflictError` → `len(successes) == 0` → assertion fails

**To safely re-run NB05**, either:
1. Change `ISOLATION_DATE` to a new far-future date: `date.today() + timedelta(days=60)`
2. Or manually clear the reservation before re-running:
   ```sql
   -- In psql or any SQL client:
   DELETE FROM room_reservations
   WHERE room_id = (SELECT room_id FROM rooms WHERE room_code = 'OR-1')
     AND reservation_start::date = '2026-04-03';  -- your ISOLATION_DATE
   ```

---

## Troubleshooting

### "kernel not found: or-scheduler"
```bash
# Re-register the kernel
uv run python -m ipykernel install --user --name or-scheduler --display-name "or-scheduler"
```

### "ValidationError: database_url field required"
The `.env` file isn't being found. Verify it exists at the project root:
```bash
cat .env          # should show DATABASE_URL=postgresql://...
```
The `config.py` uses `find_dotenv()` to search parent directories, so it should work from `Assignment/`. If not:
```bash
cd /path/to/OR-Scheduling-Data-Storage-System
uv run jupyter nbconvert ...   # run from project root, not from Assignment/
```

### "could not connect to server"
```bash
docker-compose ps          # check container status
docker-compose up -d       # restart if needed
docker-compose logs db     # check for errors
```

### Notebook fails because of stale state (reservations from prior run)
Run NB03's cleanup block manually:
```python
from sqlalchemy import text
from or_scheduler.database import SessionLocal

with SessionLocal() as s:
    s.execute(text("DELETE FROM override_displaced_appointments"))
    s.execute(text("DELETE FROM overrides"))
    s.execute(text("DELETE FROM audit_log"))
    s.execute(text("DELETE FROM equipment_reservations"))
    s.execute(text("DELETE FROM staff_reservations"))
    s.execute(text("DELETE FROM room_reservations"))
    s.execute(text("DELETE FROM appointments"))
    s.execute(text("DELETE FROM cases"))
    s.commit()
print("Cleared.")
```

### Performance test is slow (> 30 s for 10,000 cases)
Increase batch size in `test_create_case_performance`:
```python
BATCH_SIZE = 200   # default 100 — larger batches amortise commit overhead
```
Or check that `ECHO_SQL=false` in `.env` (printing SQL to console adds significant overhead).
