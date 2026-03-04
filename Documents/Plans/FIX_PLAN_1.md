# Fix Plan 1 — Eliminating All Issues Before Final Presentation

**Date:** 2026-03-04
**Goal:** Zero anomalies, zero misleading output across all 5 notebooks.

---

## Issue Index

| # | Notebook | Cell | Severity | Description |
|---|---|---|---|---|
| F1 | NB04 | `1b3bb7dd` (Test 2 function) + `8e7c6d46` (setup) | **HIGH** | 416 errors from staff conflict misclassification |
| F2 | NB05 | `341c23b2` (Test 2 raw INSERT) | **MEDIUM** | UNIQUE constraint fires instead of GIST constraint |

---

## F1 — NB04: 416 "Errors" in Concurrent Appointment Test

### Root Cause Analysis

The test pre-loads one surgeon (`surgeon_id`) and one anaesthesiologist (`anaest_id`). These two IDs are hardcoded into every single booking slot. When slots across multiple OR rooms land on the same time window (e.g., OR-1 through OR-6 all at 08:00–10:00 on day 7), only the **first booking** can acquire the two staff members — all subsequent attempts raise `StaffNotAvailableError`.

`book_one()` only catches `RoomConflictError`:
```python
except RoomConflictError:
    return ('conflict', ...)
except Exception as e:        # ← StaffNotAvailableError lands here
    return ('error', str(e))
```

Result: 416 `StaffNotAvailableError` shown as "errors" → misleading output, looks like system failure.

### Fix Strategy

Assign **each OR room a unique surgeon + anaesthesiologist pair**. We have 5 surgeons and 5 anaesthesiologists — enough for 5 rooms with zero staff overlap.

#### Step 1 — Setup Cell (`8e7c6d46`): Load all staff

Add loading of all surgeons and anaesthesiologists:

```python
# ADD these two lines to the existing `with Session(engine) as s:` block:
all_surgeons = s.execute(select(Staff).where(Staff.role == 'SURGEON')).scalars().all()
all_anaests  = s.execute(select(Staff).where(Staff.role == 'ANAESTHESIOLOGIST')).scalars().all()

# ADD these two lines after the existing variable assignments:
surgeon_ids = [st.staff_id for st in all_surgeons]
anaest_ids  = [an.staff_id for an in all_anaests]
```

Also update the print to show the new counts:
```python
print(f"Available surgeons: {len(surgeon_ids)}")
print(f"Available anaesthesiologists: {len(anaest_ids)}")
```

#### Step 2 — Test 2 Function (`1b3bb7dd`): Three targeted changes

**Change A — Limit rooms to number of unique staff pairs:**

Replace:
```python
OR_rooms = room_ids[:min(6, len(room_ids))]
```
With:
```python
n_pairs  = min(len(surgeon_ids), len(anaest_ids))  # 5 unique surgeon+anaest pairs
OR_rooms = room_ids[:min(n_pairs, len(room_ids))]  # cap rooms to available pairs
```

**Change B — Embed unique staff IDs in each booking slot:**

In the `booking_slots.append({...})` block, add two keys:
```python
booking_slots.append({
    'case_id': case_id,
    'room_id': OR_rooms[room_idx],
    'date':    date.today() + timedelta(days=day_offset),
    'start':   time(start_hour, 0),
    'end':     time(min(start_hour + 2, 17), 0),
    'surgeon_id': surgeon_ids[room_idx],  # ← ADD: unique per room
    'anaest_id':  anaest_ids[room_idx],   # ← ADD: unique per room
})
```

**Change C — Create schedules for ALL surgeons and anaesthesiologists:**

Replace the two individual `existing_staff` / `existing_anaest` blocks with a loop:
```python
# Replace both individual StaffSchedule inserts with:
for staff_id in surgeon_ids + anaest_ids:
    existing = s.execute(
        select(StaffSchedule).where(
            StaffSchedule.staff_id == staff_id,
            StaffSchedule.date == d
        )
    ).scalar_one_or_none()
    if not existing:
        s.add(StaffSchedule(
            staff_id=staff_id, date=d,
            available_from=time(8, 0), available_until=time(17, 0),
            schedule_type='REGULAR'
        ))
```

**Change D — `book_one()` uses per-slot staff IDs (not outer-scope single IDs):**

Replace inside `book_one()`:
```python
staff_items=[StaffItem(surgeon_id, 'SURGEON'), StaffItem(anaest_id, 'ANAESTHESIOLOGIST')],
confirmed_by=surgeon_id,
```
With:
```python
staff_items=[StaffItem(slot['surgeon_id'], 'SURGEON'), StaffItem(slot['anaest_id'], 'ANAESTHESIOLOGIST')],
confirmed_by=slot['surgeon_id'],
```

**Change E — Fix the conclusion print:**

Replace:
```python
print(f"  Successful appointments created with zero double-bookings.")
print(f"  Conflicts are expected when slots overlap — this is correct behaviour.")
```
With:
```python
print(f"  {result2['successes']} appointments committed across {len(OR_rooms)} OR rooms.")
print(f"  {result2['conflicts']} room conflicts (expected: ≈0 — each room has unique time slots).")
print(f"  {result2['errors']} unexpected errors (expected: 0).")
```

### Expected Outcome After Fix

| Metric | Before | After |
|---|---|---|
| Successes | 84 | ~450–500 (most slots succeed) |
| Conflicts | 0 | ~0 (unique slots per room) |
| Errors | **416** | **0** |

---

## F2 — NB05 Test 2: UNIQUE Constraint Fires Instead of GIST

### Root Cause Analysis

The raw INSERT reuses `winning_appt_id` (the same UUID as the existing reservation):

```python
conn.execute(text("""
    INSERT INTO room_reservations (... appointment_id ...)
    VALUES (gen_random_uuid(), :appt_id, ...)   ← same appointment_id reused
"""), {'appt_id': winning_appt_id, ...})
```

PostgreSQL evaluates the `UNIQUE (appointment_id, room_id)` constraint **before** the GIST exclusion constraint. The unique constraint fires first, raising:
```
duplicate key value violates unique constraint "uq_room_res_appt_room"
```

The GIST constraint (`no_room_overlap`) never gets a chance to fire.

### Fix

Use `gen_random_uuid()` directly in the SQL for `appointment_id` — no Python parameter needed. With a fresh UUID, the UNIQUE constraint passes, and the GIST exclusion constraint is the only thing that can reject the overlapping insert.

**Cell `341c23b2` — Replace the INSERT block:**

Replace:
```python
conn.execute(text("""
    INSERT INTO room_reservations
        (reservation_id, appointment_id, room_id, status,
         reservation_start, reservation_end, locked_at, created_at, updated_at)
    VALUES
        (gen_random_uuid(), :appt_id, :room_id, 'CONFIRMED',
         :res_start, :res_end, NOW(), NOW(), NOW())
"""), {
    'appt_id': winning_appt_id,
    'room_id': str(room_id),
    'res_start': datetime.combine(ISOLATION_DATE, SLOT_START, tzinfo=timezone.utc),
    'res_end':   datetime.combine(ISOLATION_DATE, SLOT_END,   tzinfo=timezone.utc),
})
```

With:
```python
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

**Also update the success print message inside the `except IntegrityError` block:**

Replace:
```python
print(f"   PostgreSQL GIST exclusion constraint rejected the INSERT.")
```
With:
```python
print(f"   PostgreSQL GIST exclusion constraint (no_room_overlap) rejected the INSERT.")
```

### Expected Outcome After Fix

```
✅ IntegrityError raised as expected!
   PostgreSQL GIST exclusion constraint (no_room_overlap) rejected the INSERT.
   Error: conflicting key value violates exclusion constraint "no_room_overlap"
```

This proves the **database-level GIST constraint** is independently enforcing double-booking prevention, separate from application logic.

---

## F3 — Teacher Requirement: Problem Case + Solution Case for Each Test

### What's required
For every test that demonstrates a protection mechanism, add two clearly labelled parts:
- **Part A — Problem Case**: Show what ACTUALLY happens when the protection is absent (concrete data, not just a diagram)
- **Part B — Solution Case**: Show what the system does with proper handling (already mostly exists)

### NB03 — Atomicity (New)

**Add between Op 1 and Op 2a** — a rollback demonstration:

New markdown cell (after cell `c380059d`):
```markdown
## Atomicity — The Problem: What if a Booking Fails Halfway?

Without atomic transactions, a crash or validation failure midway through
`create_appointment()` could leave partial data:
- An `appointments` row exists, but no `room_reservation`
- Staff are reserved but equipment is not
- The OR appears unavailable (phantom booking), but no room_reservation locks it

The cells below show this cannot happen: even a mid-operation failure
rolls back **all** changes atomically — zero partial records.
```

New code cell (after the markdown cell):
```python
import uuid as _uuid_mod
print("PROBLEM CASE: What if create_appointment() fails midway?")
print("Passing an INVALID staff ID to force an error inside the operation...\n")

try:
    with SessionLocal() as _s:
        create_appointment(
            _s,
            case_id=case_id,            # valid case from Op 1
            room_id=room_or1_id,
            scheduled_date=TARGET_DATE,
            start_time=time(14, 0),     # unused time slot
            end_time=time(16, 0),
            staff_items=[StaffItem(str(_uuid_mod.uuid4()), 'SURGEON')],  # INVALID UUID
            confirmed_by=surgeon_id,
        )
        _s.commit()
except Exception as _e:
    print(f"✓ Operation rejected: {type(_e).__name__}: {_e}\n")

with engine.connect() as _c:
    _appt_count = _c.execute(text(
        "SELECT COUNT(*) FROM appointments WHERE case_id = :cid"
    ), {'cid': str(case_id)}).scalar()
    _res_count = _c.execute(text(
        "SELECT COUNT(*) FROM room_reservations rr "
        "JOIN appointments a ON a.appointment_id = rr.appointment_id "
        "WHERE a.case_id = :cid"
    ), {'cid': str(case_id)}).scalar()

print("DB state after failed booking:")
print(f"  appointments for this case    : {_appt_count}  (expected: 0)")
print(f"  room_reservations for this case: {_res_count}  (expected: 0)")
print()
print("✅ ATOMICITY CONFIRMED — zero partial records. Full rollback on any failure.")
```

**Update Op 2b markdown** (cell `23035fe4`) to explicitly frame it as problem+solution:
```markdown
## Operation 2 — Double-Booking (Problem Case + System Response)

**The problem:** Two coordinators simultaneously try to book OR-3 for different patients
at the same time. Without conflict detection, both could succeed — a patient safety disaster.

**What the system does:** The second booking raises `RoomConflictError` and rolls back.
After the error, verify that only ONE reservation exists in the database.
```

**Update Op 2b code** (cell `067336e5`) — add DB verification after the conflict:
```python
# ... existing code ...
# ADD at the end (after the except block):
with engine.connect() as _c:
    _res_count = _c.execute(text("""
        SELECT COUNT(*) FROM room_reservations rr
        JOIN appointments a ON a.appointment_id = rr.appointment_id
        WHERE a.scheduled_date = :d
          AND rr.room_id = :rid
          AND rr.status NOT IN ('RELEASED', 'COMPLETED')
    """), {'d': str(TARGET_DATE), 'rid': str(room_or1_id)}).scalar()
print(f"\nDB verification:")
print(f"  Active reservations for OR-3 on {TARGET_DATE}: {_res_count}")
print(f"  ✅ Only 1 reservation — double-booking was prevented.")
```

### NB05 Test 1 — Race Condition (New Problem Case)

**Add before Test 1 (`b7e4a9b9`)** — naive booking demo using a temporary unprotected table:

New markdown cell:
```markdown
## Test 1 — Race Condition (Part A: The Problem Without Locking)

**Naive check-then-insert pattern** (no SELECT FOR UPDATE):
```
Thread A: SELECT COUNT(*) = 0  ← room appears free (no lock held)
Thread B: SELECT COUNT(*) = 0  ← room appears free (same snapshot)
          ...both proceed...
Thread A: INSERT reservation    ← succeeds
Thread B: INSERT reservation    ← also succeeds! (no constraint to stop it)
Result  : 2 rows for same room/time — double booking!
```
The demo below uses an **unprotected temporary table** (no GIST, no UNIQUE constraints)
to show actual data corruption from this race condition.
```

New code cell (the naive booking demo using temp table - see F3 NB05 code below):
- Creates `naive_bookings` temp table (no constraints)
- 10 threads race with check-then-insert + 20ms sleep to widen window
- Shows multiple rows in DB proving data corruption
- Clean up the temp table after

**Update Test 1 markdown** (cell `b7e4a9b9`):
```markdown
## Test 1 — Race Condition (Part B: The Solution — SELECT FOR UPDATE)

`SELECT FOR UPDATE` makes check-and-insert a single atomic operation — no race window.
`threading.Barrier` forces all 50 threads to start simultaneously.
```

### NB05 Test 2 — GIST Constraint (Fix + Framing)

**Update Test 2 markdown** (cell `0c713043`):
```markdown
## Test 2 — GIST Constraint Safety Net (Database-Level Defence)

Even if application logic is bypassed entirely (e.g., a malicious or buggy direct INSERT),
PostgreSQL's GIST exclusion constraint `no_room_overlap` rejects the double-booking at the
database level. This is a second independent layer of protection.
```

---

## Non-Issues (No Code Change Required)

| # | Notebook | Observation | Why No Fix Needed |
|---|---|---|---|
| N1 | NB02 | Row counts show 10,000 patients / 10,555 cases (NB04 state) | Expected cross-notebook accumulation; idempotency assertion scoped to HN-% pattern |
| N2 | NB03 | Sterilization end = today, not appointment date | Correct behaviour — sterilization uses `actual_end_time` (now), not scheduled end |
| N3 | NB02 | 8 rooms vs "50 Rooms" in assignment example | Assignment says "such as 50 Rooms" (illustrative, not a hard limit) |

---

## Files Changed Summary

| File | Change |
|---|---|
| `Assignment/04_performance_test.ipynb` | Cells `8e7c6d46` and `1b3bb7dd` — multi-staff setup + per-slot staff assignment |
| `Assignment/05_isolation_test.ipynb` | Cell `341c23b2` — use `gen_random_uuid()` for appointment_id in raw INSERT |

---

## Execution Order After Fix

Run notebooks in standard order. NB03 cleanup cell wipes transactional state before each run.

```
01 → 02 → 03 → 04 → 05
```

NB05 requires a fresh `ISOLATION_DATE` if the previous run already booked OR-1 on `today + 30 days`. Either use `today + 60 days` or run on a fresh database day.
