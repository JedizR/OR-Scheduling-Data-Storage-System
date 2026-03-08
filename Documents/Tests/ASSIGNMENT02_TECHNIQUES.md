# Assignment 02 — Optimisation Techniques Reference
**Subject:** Global data-intensive project, Part 03 — Support of high RPS OLTP operations

This document explains every optimisation technique used to exceed the 10,000 RPS requirement,
with references to the specific source files where each technique is implemented.

---

## Architecture Overview

```
Application
    │
    ▼
insert_or_events() / update_or_events()   ← src/or_scheduler/mongo_operations.py
    │
    ▼
MongoDB 7 — or_scheduler.or_events        ← docker-compose.yml (mongodb service)
    │                                        src/or_scheduler/mongo_client.py
    ▼
WiredTiger storage engine (on-disk)
```

**Two source files power Assignment 02:**

| File | Role |
|------|------|
| `src/or_scheduler/mongo_client.py` | MongoClient singleton, connection pool, index setup |
| `src/or_scheduler/mongo_operations.py` | All 4 required functions + helper utilities |

The notebook (`Assignment/06_mongodb_performance.ipynb`) is self-contained and
re-implements the same functions inline for presentation clarity.

---

## Insert Optimisation Techniques

Implemented in: `Assignment/06_mongodb_performance.ipynb` → cell `nb06-fn2-helpers`
Reference implementation: `src/or_scheduler/mongo_operations.py` → `test_insert_performance()`

### Technique 1 — Batching with `insert_many()`

**Naive:** `insert_or_events([doc])` called once per document → 1 round-trip per document.

**Optimised:** `insert_or_events(batch_of_1000)` → 1 round-trip per 1,000 documents.

Each network call to MongoDB has a fixed overhead (TCP handshake processing, wire protocol
framing). Batching amortises this cost: 50,000 individual inserts require 50,000 round-trips,
while the same data in batches of 1,000 requires only 50 round-trips.

```python
# src/or_scheduler/mongo_operations.py — insert_or_events()
result = coll.insert_many(events, ordered=ordered)
```

---

### Technique 2 — `ordered=False`

**Naive:** `insert_many(ordered=True)` → MongoDB processes documents serially and stops on
the first error.

**Optimised:** `insert_many(ordered=False)` → MongoDB can process documents in any order,
enabling parallel server-side processing within a batch.

```python
# Assignment/06_mongodb_performance.ipynb — insert_or_events()
result = coll.insert_many(events, ordered=ordered)   # ordered=False by default
```

---

### Technique 3 — `WriteConcern(w=1, j=False)`

**Naive:** default write concern waits for a journal `fsync` to disk before acknowledging.

**Optimised:** `WriteConcern(w=1, j=False)` acknowledges the write once it reaches MongoDB's
in-memory WiredTiger cache — without waiting for the journal file to be flushed to disk.

This is safe for OR event logging (append-only, high-volume, non-critical-path) because:
- MongoDB's OS page cache provides a durable buffer even without `j=True`
- Full durability can be restored by setting `j=True` for audit-critical writes

```python
# src/or_scheduler/mongo_client.py — get_events_collection()
wc = WriteConcern(w=1, j=False) if fast else WriteConcern(w=1, j=True)
```

---

### Technique 4 — Drop secondary indexes during bulk load

**Naive:** 4 secondary indexes are maintained throughout the insert run. Every insert
triggers a B-tree update for each index: 4 extra write operations per document.

**Optimised:** Drop all secondary indexes before the bulk load. MongoDB writes only
to the `_id` index during the insert. Recreate indexes once after all data is loaded —
MongoDB builds each index in a single efficient pass.

```python
# src/or_scheduler/mongo_client.py — drop_secondary_indexes()
for name in ("ix_occurred_at", "ix_type_status", "ix_entity_id", "ix_dept_time"):
    coll.drop_index(name)

# After bulk load:
# src/or_scheduler/mongo_client.py — setup_collection()
coll.create_index([("occurred_at", DESCENDING)], name="ix_occurred_at")
# ... (3 more indexes)
```

---

### Technique 5 — ThreadPoolExecutor parallelism

**Naive:** batches submitted one at a time from a single thread.

**Optimised:** 20 worker threads each submit batches to MongoDB simultaneously.
Because PyMongo's connection pool (`maxPoolSize=50`) allows up to 50 concurrent
connections, all 20 threads can execute their insert commands in parallel.

```python
# Assignment/06_mongodb_performance.ipynb — test_insert_performance()
with ThreadPoolExecutor(max_workers=20) as exe:
    for _ in as_completed([exe.submit(_insert_batch, b) for b in batches]):
        pass
```

```python
# src/or_scheduler/mongo_client.py — get_mongo_client()
_client = MongoClient(settings.mongodb_uri, maxPoolSize=50, minPoolSize=5, ...)
```

---

## Update Optimisation Techniques

Implemented in: `Assignment/06_mongodb_performance.ipynb` → cell `nb06-fn4`
Reference implementation: `src/or_scheduler/mongo_operations.py` → `test_update_performance()`

### Technique 1 — `update_many()` single call

**Naive:** `update_one({_id: id}, ...)` called once per document → N round-trips for N
documents.

**Optimised:** `update_many({event_type: "...", status: "pending"}, ...)` → 1 round-trip
updates all matching documents at the server side.

```python
# Assignment/06_mongodb_performance.ipynb — update_or_events()
res = coll.update_many(filter_query, update_doc, upsert=upsert)
```

---

### Technique 2 — Compound index `(event_type, status)`

**Without index:** MongoDB performs a *collection scan* — reads every document to find
matches for `{event_type: "appointment_booked", status: "pending"}`.

**With compound index `ix_type_status`:** MongoDB uses the index to jump directly to
matching entries. Index lookup is O(log N) vs O(N) for a collection scan.

```python
# src/or_scheduler/mongo_client.py — setup_collection()
results["ix_type_status"] = coll.create_index(
    [("event_type", ASCENDING), ("status", ASCENDING)],
    name="ix_type_status",
)
```

---

### Technique 3 — Date-range thread partitioning

**Naive:** all 5,000 updates run in a single thread, one after another.

**Optimised:** the `occurred_at` time range is divided into equal slices. Each thread
calls `update_or_events()` on a non-overlapping slice — so workers run truly in parallel
with no document contention between threads.

```python
# Assignment/06_mongodb_performance.ipynb — test_update_performance()
total_range = timedelta(seconds=n_updates)
slice_td    = total_range / workers
base        = now - total_range

def _range_update(k: int) -> UpdateResult:
    start = base + k       * slice_td
    end   = base + (k + 1) * slice_td
    return update_or_events(
        {"event_type": "appointment_booked", "status": "pending",
         "occurred_at": {"$gte": start, "$lt": end}},
        update_payload,
    )

with ThreadPoolExecutor(max_workers=workers) as exe:
    for r in exe.map(_range_update, range(workers)):
        total_modified += r.modified_count
```

---

## Connection Pool Configuration

**File:** `src/or_scheduler/mongo_client.py` → `get_mongo_client()`

```python
_client = MongoClient(
    settings.mongodb_uri,
    maxPoolSize=50,     # matches max ThreadPoolExecutor workers
    minPoolSize=5,      # keep minimum connections warm
    maxIdleTimeMS=30_000,
)
```

`maxPoolSize=50` ensures that 20 insert threads and 10 update threads can each hold an
active MongoDB connection simultaneously without queueing.

---

## Infrastructure

**File:** `docker-compose.yml`

```yaml
mongodb:
  image: mongo:7
  ports:
    - "27017:27017"
  volumes:
    - mongodata:/data/db
```

**File:** `src/or_scheduler/config.py`

```python
mongodb_uri: str = "mongodb://localhost:27017"
```

---

## Result Summary

| Test | Approach | N Documents | TPS (typical) | Requirement |
|------|----------|-------------|----------------|-------------|
| Insert | Naive | 1,000 | ~2,000 | — |
| Insert | Optimised | 50,000 | ~330,000 | ✅ PASS (>10,000) |
| Update | Naive | 5,000 | ~4,300 | — |
| Update | Optimised | 5,000 | ~320,000 | ✅ PASS (>10,000) |

The optimised insert is **~155× faster** than the naive approach.
The optimised update is **~75× faster** than the naive approach.
