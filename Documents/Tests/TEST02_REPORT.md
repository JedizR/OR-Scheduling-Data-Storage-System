# Test Report — Assignment 02: High-RPS MongoDB OLTP Performance
**Date:** 2026-03-08
**Stack:** MongoDB 7.0.30 · PyMongo 4.16.0 · Python 3.10.18
**Hardware:** Apple M-series (NVMe SSD, OS page cache)
**Notebook:** `Assignment/06_mongodb_performance.ipynb`

---

## Overall Status

| Function | Test | Documents | TPS | Meets >10,000 RPS? |
|----------|------|-----------|-----|-------------------|
| `insert_or_events` | Smoke-test (5 docs) | 5 | — | ✅ PASS |
| `test_insert_performance` | Naive (insert_one × N) | 50,000 | 3,818 | — (baseline) |
| `test_insert_performance` | Optimised | 50,000 | **240,740** | ✅ PASS |
| `update_or_events` | Smoke-test (3 docs) | 3 | — | ✅ PASS |
| `test_update_performance` | Naive (update_one × N) | 5,000 | 4,018 | — (baseline) |
| `test_update_performance` | Optimised | 5,000 | **315,503** | ✅ PASS |

---

## Setup

- MongoDB connected: version 7.0.30 at `mongodb://localhost:27017`
- Collection `or_scheduler.or_events` reset and 4 indexes created:
  - `ix_occurred_at` — `occurred_at DESC`
  - `ix_type_status` — `(event_type ASC, status ASC)` ← compound index used by update optimised
  - `ix_entity_id`   — `entity_id ASC`
  - `ix_dept_time`   — `(department_id ASC, occurred_at DESC)`

---

## Function 1 — `insert_or_events()`

Smoke-test: inserted 5 documents, acknowledged=True, duration=0.73 ms ✅

---

## Function 2 — `test_insert_performance()`

Both approaches ran against the **same 50,000 documents** for a fair comparison.

| Approach | Documents | TPS | Duration (s) | Meets >10,000 RPS? |
|----------|-----------|-----|--------------|-------------------|
| Naive    | 50,000    | 3,818 | 13.095 | — |
| Optimised | 50,000   | **240,740** | 0.208 | ✅ PASS |

**Speedup: 63× faster than naive**

### Optimisation techniques applied

| # | Technique | Effect |
|---|-----------|--------|
| 1 | `insert_many(batch=1,000)` | 50,000 round-trips → 50 (1,000× fewer network calls) |
| 2 | `ordered=False` | Parallel server-side processing within each batch |
| 3 | `WriteConcern(w=1, j=False)` | No journal fsync wait — write acknowledged from WiredTiger memory cache |
| 4 | Drop secondary indexes during load | No B-tree update per insert; indexes rebuilt in one pass after load |
| 5 | `ThreadPoolExecutor(20 workers)` | 20 threads submit batches concurrently via `maxPoolSize=50` connection pool |

---

## Function 3 — `update_or_events()`

Smoke-test: matched=3, modified=3, duration=1.11 ms ✅

---

## Function 4 — `test_update_performance()`

Both approaches ran against the **same 5,000 documents** (aligned with assignment spec: "few thousands").

| Approach | Documents | TPS | Duration (s) | Meets >10,000 RPS? |
|----------|-----------|-----|--------------|-------------------|
| Naive    | 5,000     | 4,018 | 1.244 | — |
| Optimised | 5,000    | **315,503** | 0.016 | ✅ PASS |

**Speedup: 79× faster than naive**

### Optimisation techniques applied

| # | Technique | Effect |
|---|-----------|--------|
| 1 | `update_many()` single call | 1 round-trip to modify all matching documents vs N round-trips |
| 2 | Compound index `(event_type, status)` | Filter uses index instead of full collection scan |
| 3 | Date-range thread partitioning | 10 workers each update a non-overlapping `occurred_at` slice in parallel — no document contention |

---

## Anomaly Analysis

| Observation | Normal? | Explanation |
|-------------|---------|-------------|
| Naive insert 13s vs optimised 0.2s (same 50k docs) | ✅ Yes | 49,950 extra round-trips + 4× B-tree updates per doc + journal fsync overhead |
| Update optimised 0.016s (5k docs) | ✅ Yes | Single `update_many` call + 10 parallel threads — MongoDB processes entirely in server memory without client round-trip overhead |

---

## Assignment 02 Requirement Coverage

| Requirement | Delivered | Met? |
|-------------|-----------|------|
| Support >10,000 RPS | Insert optimised: 240,740 TPS · Update optimised: 315,503 TPS | ✅ |
| Use MongoDB / Redis / MPP NoSQL | MongoDB 7 (primary store) | ✅ |
| `insert_or_events()` function | Defined with `ordered`, `write_concern` parameters; smoke-test passed | ✅ |
| `test_insert_performance()` — few tens of thousands | 50,000 documents, naive vs optimised, PASS assertion | ✅ |
| `update_or_events()` function | Defined with `filter_query`, `update_fields`, `upsert`; smoke-test passed | ✅ |
| `test_update_performance()` — few thousands | 5,000 documents, naive vs optimised, PASS assertion | ✅ |

---

## Final Verdict

**All requirements met.** Both optimised functions exceed 10,000 RPS by a factor of 24–31×.
The naive baselines are included to demonstrate the concrete cost of each missing optimisation,
making the test pedagogically complete for presentation.
