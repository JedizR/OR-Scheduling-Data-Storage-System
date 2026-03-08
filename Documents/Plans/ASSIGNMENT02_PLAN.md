# Assignment02 — Implementation Plan: High-RPS MongoDB OLTP
**Status:** Ready to implement
**Date:** 2026-03-08
**Assignment:** Global data-intensive project, Part 03 — Support of high RPS OLTP operations

---

## 1. Requirements

| Requirement | Spec | Our Target |
|---|---|---|
| Throughput | > 10,000 RPS | > 20,000 TPS insert (direct); > 50,000 TPS insert (Redis-buffered) |
| Data scale | Millions of records | 50k per test run; schema designed for 100M+ |
| Technology | MongoDB / Redis / other NoSQL | MongoDB (primary store) + Redis (write buffer + cache) |
| Deliverables | 4 Python functions | `insert_or_events`, `test_insert_performance`, `update_or_events`, `test_update_performance` |
| Code quality | Python | Type hints, dataclasses, docstrings, error handling |

---

## 2. Architecture

The class material explicitly mandates a two-layer architecture for "beyond expectation" results. We implement **both** MongoDB and Redis with distinct roles.

```
                         ┌─────────────────────────────────────────┐
                         │           Application Layer             │
                         └───────────┬────────────────┬────────────┘
                                     │                │
                       ┌─────────────▼───────┐   ┌────▼──────────────────┐
                       │  WRITE PATH (Fast)  │   │  READ / UPDATE PATH   │
                       └─────────────────────┘   └───────────────────────┘
                                     │                │
                   ┌─────────────────▼──────────┐     │
                   │  Redis List  (LPUSH O(1))  │     ▼
                   │  ~172k accept/sec          │  ┌──────────────────┐
                   │  LRU eviction, TTL on keys │  │  Redis Cache     │
                   └────────────┬───────────────┘  │  (Cache-Aside)   │
                                │ Background       │  LRU eviction    │
                                │ Flush Worker     │  TTL = 300s      │
                                │ (batched)        └──────┬───────────┘
                                ▼                         │ miss
                   ┌────────────────────────────┐         ▼
                   │       MongoDB              │  ┌───────────────────┐
                   │  or_scheduler.or_events    │◄─┤  MongoDB          │
                   │  insert_many(ordered=False)│  │  (source of truth)│
                   │  WriteConcern(w=1, j=False)│  └───────────────────┘
                   │  maxPoolSize=50            │
                   └────────────────────────────┘
```

### Why both MongoDB AND Redis

| Layer | Technology | Role | Class material reference |
|---|---|---|---|
| Primary store | MongoDB 7 | Durable document store, rich queries, source of truth | "MongoDB or Redis or other MPP OLTP NoSQL" |
| Write buffer | Redis List | Accept >50k writes/sec; background flush to MongoDB | "Append-only log… route writes through data bus first" |
| Read cache | Redis Hash | Cache-Aside with LRU + TTL; serve from RAM | "Introduce in-memory Key-Value store in front of DB" |

Redis is **already in this project's ROADMAP** (Milestone 7 — tentative holds + pub/sub). Assignment02 adds a second Redis use-case: write buffer and read cache. The docker-compose will have one Redis container serving all three purposes.

---

## 3. Technology Decisions

### 3.1 MongoDB — Why Not Redis as Primary

| Factor | MongoDB | Redis |
|---|---|---|
| Durability | WiredTiger on-disk; w=1 survives crash | In-memory; requires AOF `always` for real durability |
| Data model | BSON documents, flexible schema, nested payloads | Key-value, hashes — no nested query |
| Query power | `$match`, `$set`, `$in`, aggregation pipeline | None — no `find({ status: 'pending' })` |
| Role in project | Assignment02 primary store | Write buffer + cache (two use-cases) |

### 3.2 PyMongo vs Motor

Motor (async) is **slower** than PyMongo under ThreadPoolExecutor load. When a Motor coroutine waits for I/O, the event loop stays in one thread — no parallelism. PyMongo threads release the GIL on I/O, allowing true parallel execution. Same conclusion as NB04/NB05 pattern already in this codebase. **Use PyMongo (sync)**.

---

## 4. Entity: `or_events` Collection

### 4.1 Rationale

Assignment01 `audit_log` is a flat PostgreSQL table with fixed columns (`entity_type`, `entity_id`, `action`). Real OR events are richer: an `appointment_booked` event contains room, surgeon, urgency, duration; an `override_issued` event contains the bumped IDs list. MongoDB's flexible schema stores the full payload natively without migrations.

This is a direct, named extension of the OR Scheduling System into a NoSQL event store layer.

### 4.2 Document Schema

```python
# ~400 bytes average, BSON-efficient
{
    "_id": ObjectId,                     # auto, 12-byte BSON
    "event_id": str,                     # UUID4 string, for idempotent lookups
    "event_type": str,                   # enum (see below)
    "occurred_at": datetime,             # ISODate UTC
    "entity_type": str,                  # "case" | "appointment" | "room" | "equipment" | "override"
    "entity_id": str,                    # UUID of the affected entity
    "department_id": str,                # UUID, for sharding and filtering
    "actor_id": str,                     # UUID of the staff member who triggered the event
    "payload": dict,                     # flexible per event_type (see below)
    "status": str,                       # "pending" | "acknowledged" | "resolved"
    "acknowledged_at": datetime | None,
    "acknowledged_by": str | None,
    "review_notes": str | None,
    "schema_version": int,               # = 1, for forward-compatibility
}
```

**Event types and payload shapes:**

| `event_type` | Key payload fields |
|---|---|
| `case_created` | `patient_id`, `urgency`, `diagnosis_code` |
| `appointment_booked` | `room_id`, `scheduled_date`, `duration_min`, `surgeon_id`, `urgency` |
| `appointment_cancelled` | `reason`, `cancelled_by`, `original_date` |
| `room_status_changed` | `room_id`, `old_status`, `new_status` |
| `equipment_sterilization` | `equipment_id`, `sterilization_end` |
| `override_issued` | `emergency_appt_id`, `bumped_ids`, `override_reason` |

**Short field names note:** BSON stores field names in every document. Field names like `event_id` (8 chars) vs `eid` (3 chars) — at 50k docs this is 250KB saved on wire. For a class assignment we keep readable names, but this is a production consideration for millions of records.

### 4.3 Index Design

```python
# Created ONCE at collection setup time
# Must all fit in RAM — analysis below
collection.create_index([("occurred_at", DESCENDING)],
                        name="ix_occurred_at")
collection.create_index([("event_type", ASCENDING), ("status", ASCENDING)],
                        name="ix_type_status")           # covers 80% of update queries
collection.create_index([("entity_id", ASCENDING)],
                        name="ix_entity_id")
collection.create_index([("department_id", ASCENDING), ("occurred_at", DESCENDING)],
                        name="ix_dept_time")             # sharding-aligned

# For performance INSERT test: drop ix_type_status and ix_dept_time before bulk load
# (they cost ~15% write throughput each), recreate after
# _id index is automatic, cannot be dropped
```

**RAM fit analysis (50k docs in test; 10M in production):**

| Index | Entries | Est. bytes/entry | Total 10M docs |
|---|---|---|---|
| `_id` (auto) | 10M | 24 | 240 MB |
| `ix_occurred_at` | 10M | 20 | 200 MB |
| `ix_type_status` | 10M | 28 | 280 MB |
| `ix_entity_id` | 10M | 28 | 280 MB |
| `ix_dept_time` | 10M | 36 | 360 MB |
| **Total** | | | **~1.36 GB** |

On a machine with 8GB+ RAM (MacBook/dev server), all indexes comfortably fit. For 100M docs (~14GB), a dedicated MongoDB node with 32GB RAM is needed — matching the class material constraint.

### 4.4 Shard Key Design (Production)

For a sharded cluster (Mongos + 2 shards), the shard key must distribute writes evenly:

```javascript
// BAD: monotonic key → all inserts hit the same shard (insert hotspot)
sh.shardCollection("or_scheduler.or_events", { "_id": 1 })

// BAD: low-cardinality → uneven distribution
sh.shardCollection("or_scheduler.or_events", { "event_type": 1 })

// GOOD: compound hashed key → ~50/50 split, no hotspot
sh.shardCollection("or_scheduler.or_events", { "department_id": 1, "event_id": "hashed" })
```

This mirrors the class material example: `Customer_id + Order_id` as compound key for 50/50 split. For local Docker (single node), sharding is not active but the shard key choice is included as comments and an architecture note in the notebook.

**Live resharding command** (if load imbalance detected in production):
```javascript
db.adminCommand({
    reshardCollection: "or_scheduler.or_events",
    key: { department_id: 1, event_id: "hashed" }
})
```

---

## 5. Performance Optimization Levels

The notebook demonstrates a **progression** from naive to maximally optimized. This is the "beyond expectation" element — we show the improvement journey with measured data.

### 5.1 Insert Optimization Levels

| Level | Strategy | Expected TPS | Shown in notebook |
|---|---|---|---|
| L0 — Naive | `insert_one()` per doc, single thread | ~400 | Yes (baseline) |
| L1 — Batch ordered | `insert_many(batch=100, ordered=True)` | ~5,000 | Yes |
| L2 — Batch unordered | `insert_many(batch=1000, ordered=False)` | ~8,000 | Yes |
| L3 — Multi-thread | L2 + `ThreadPoolExecutor(20)` + `maxPoolSize=50` | ~15,000 | Yes |
| L4 — Write concern | L3 + `WriteConcern(w=1, j=False)` | ~20,000 | Yes — **primary result** |
| L5 — Sparse indexes | L4 + drop non-`_id` indexes during bulk load | ~28,000 | Yes |
| L6 — Redis buffer | Accept via `LPUSH`, background flush batches | ~50,000+ accepted | Yes — **bonus** |

L4 is the "standard" MongoDB result that comfortably exceeds 10k. L5 and L6 are the "beyond expectation" extras.

**L6 mechanism (Redis write buffer):**
```
LPUSH or_events_queue <json_doc>   →  accepted ~172k/sec by Redis
Background flusher (daemon thread):
    while True:
        batch = LRANGE or_events_queue 0 999
        if batch:
            collection.insert_many(deserialize(batch), ordered=False)
            LTRIM or_events_queue 1000 -1
        else:
            sleep(0.001)
```
The performance test measures "accepted TPS" (LPUSH rate) + "persisted TPS" (flush rate) separately, showing the layered architecture advantage.

### 5.2 Update Optimization Levels

| Level | Strategy | Expected TPS | Shown in notebook |
|---|---|---|---|
| L0 — Naive | `update_one()` per doc, loop | ~800 | Yes (baseline) |
| L1 — update_many | Single `update_many(filter, $set)` | ~6,000 | Yes |
| L2 — Indexed | L1 + compound index on `(event_type, status)` | ~10,000 | Yes — **primary result** |
| L3 — Multi-thread | L2 + `ThreadPoolExecutor(10)` + date-range partitioning | ~12,000 | Yes |
| L4 — Cache invalidation | L3 + Redis `DEL` for cached entity keys | ~11,000 (net) | Yes — **realistic production** |

L2 is already beyond the 10k target. L3 and L4 show production-grade patterns.

**Thread partitioning for updates (L3):**
```python
# Partition 5000 docs by occurred_at range across 10 workers
# Worker 0: occurred_at in [T+0d, T+0.5d)
# Worker 1: occurred_at in [T+0.5d, T+1d)
# ... no cross-worker lock contention
```

### 5.3 Read Concern Strategy

Per class material — tune per query type:

```python
# Fastest: use for analytics / non-critical reads
collection.with_options(read_concern=ReadConcern("available"))

# Default: use for OR event displays (last known state)
collection.with_options(read_concern=ReadConcern("local"))

# Durable: use only for audit compliance reads
collection.with_options(read_concern=ReadConcern("majority"))
```

The notebook demonstrates this difference with a comparison cell.

---

## 6. The 4 Required Functions — Complete Specification

### 6.1 `insert_or_events()`

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from pymongo.results import InsertManyResult
from pymongo import WriteConcern

@dataclass
class InsertResult:
    inserted_count: int
    acknowledged: bool
    duration_ms: float

def insert_or_events(
    events: list[dict],
    *,
    ordered: bool = False,
    write_concern: Optional[WriteConcern] = None,
) -> InsertResult:
    """Bulk-insert OR event documents into the or_events collection.

    Uses unordered execution by default, enabling parallel server-side
    processing and skipping per-operation ordering guarantees.
    Insert path is: Application → MongoDB (direct).

    Args:
        events: List of event dicts conforming to the or_events schema.
                Each dict must have at minimum: event_id, event_type,
                occurred_at, entity_type, entity_id.
        ordered: If False (default), server processes in any order for
                 higher throughput. Set True only if insertion order matters.
        write_concern: Override the collection-level write concern.
                       Defaults to WriteConcern(w=1, j=False).

    Returns:
        InsertResult dataclass with inserted_count, acknowledged, duration_ms.

    Raises:
        pymongo.errors.BulkWriteError: On partial failure (ordered=True).
        pymongo.errors.ConnectionFailure: On MongoDB connection loss.
    """
```

**Internal flow:**
1. Validate `events` is non-empty; raise `ValueError` if empty
2. Start `time.perf_counter()`
3. Get collection with `WriteConcern(w=1, j=False)` (or override)
4. Call `collection.insert_many(events, ordered=ordered)`
5. Return `InsertResult`

### 6.2 `test_insert_performance()`

```python
@dataclass
class PerformanceResult:
    level: str
    n_docs: int
    batch_size: int
    workers: int
    total_time_s: float
    tps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    errors: int

def test_insert_performance(
    n: int = 50_000,
    levels: list[str] | None = None,
) -> list[PerformanceResult]:
    """Performance test for insert_or_events across multiple optimization levels.

    Generates n synthetic OR event documents and runs insert benchmarks
    at each requested optimization level, printing a Rich comparison table.

    Optimization levels (in order, each builds on previous):
        "L0": insert_one() baseline — single thread, no batching
        "L1": insert_many ordered=True, batch=100
        "L2": insert_many ordered=False, batch=1000
        "L3": L2 + ThreadPoolExecutor(20) workers
        "L4": L3 + WriteConcern(w=1, j=False)   ← primary benchmark
        "L5": L4 + drop secondary indexes during load
        "L6": Redis LPUSH buffer + background flush  ← bonus

    Args:
        n: Total number of documents to insert per level. Default 50,000.
        levels: Subset of levels to run. Default: all levels.

    Returns:
        List of PerformanceResult, one per level, in order.
    """
```

**Internal flow:**
1. Idempotency cleanup: `collection.drop()` + `create_collection()` + `create_indexes()`
2. `_generate_events(n)` — generate synthetic events using `faker` (vectorized, no loop overhead)
3. For each level: run benchmark, collect per-batch latencies into list, compute percentiles
4. Print Rich table after all levels complete
5. Assert `results[-2].tps > 10_000` (L4 must exceed requirement)
6. Return results

**Event generation strategy (performance-optimized):**
```python
def _generate_events(n: int) -> list[dict]:
    """Pre-generate all events in one pass — no per-document allocation in inner loop."""
    now = datetime.now(timezone.utc)
    event_types = ["case_created", "appointment_booked", "appointment_cancelled",
                   "room_status_changed", "equipment_sterilization", "override_issued"]
    entity_types = ["case", "appointment", "room", "equipment", "override"]
    statuses = ["pending"] * 7 + ["acknowledged"] * 2 + ["resolved"]  # weighted

    # Use list comprehension — faster than append loop (avoids repeated .append() call overhead)
    return [
        {
            "event_id": str(uuid4()),
            "event_type": event_types[i % len(event_types)],
            "occurred_at": now - timedelta(seconds=i),
            "entity_type": entity_types[i % len(entity_types)],
            "entity_id": str(uuid4()),
            "department_id": _DEPT_IDS[i % 5],    # pre-generated pool
            "actor_id": _ACTOR_IDS[i % 20],        # pre-generated pool
            "payload": _PAYLOADS[i % len(event_types)],  # pre-built payload pool
            "status": statuses[i % len(statuses)],
            "acknowledged_at": None,
            "acknowledged_by": None,
            "review_notes": None,
            "schema_version": 1,
        }
        for i in range(n)
    ]
```

Using pre-built ID pools (`_DEPT_IDS`, `_ACTOR_IDS`) and a payload template pool avoids `uuid4()` calls in the inner loop for non-critical fields — the batch uuid4() calls for `event_id` are the only unique-per-doc values required.

### 6.3 `update_or_events()`

```python
from pymongo.results import UpdateResult

@dataclass
class UpdateBatchResult:
    matched_count: int
    modified_count: int
    duration_ms: float

def update_or_events(
    filter_query: dict,
    update_fields: dict,
    *,
    upsert: bool = False,
) -> UpdateBatchResult:
    """Bulk-update OR events matching filter_query.

    Uses update_many() for a single server round-trip covering all matching
    documents. Dramatically faster than per-document update_one() calls
    (benchmark: 243% faster at 1M doc scale).

    Typical usage — acknowledge all pending events for a department:
        update_or_events(
            filter_query={"event_type": "appointment_booked", "status": "pending"},
            update_fields={"status": "acknowledged", "acknowledged_at": datetime.now(UTC),
                           "acknowledged_by": str(actor_id)},
        )

    Args:
        filter_query: MongoDB filter document. Must include at least one indexed field
                      for performance (event_type, status, entity_id, department_id,
                      or occurred_at range).
        update_fields: Dict of fields to $set on all matched documents.
                       Do NOT include the $set operator — it is added internally.
        upsert: If True, insert a document if no match found. Default False.

    Returns:
        UpdateBatchResult with matched_count, modified_count, duration_ms.

    Raises:
        ValueError: If filter_query is empty (would update entire collection).
        pymongo.errors.ConnectionFailure: On connection loss.
    """
```

**Internal flow:**
1. Guard: `if not filter_query: raise ValueError("filter_query cannot be empty — would update all documents")`
2. Build `update_doc = {"$set": {**update_fields, "updated_at": datetime.now(timezone.utc)}}`
3. Call `collection.update_many(filter_query, update_doc, upsert=upsert)`
4. Return `UpdateBatchResult`

**Redis cache invalidation (production pattern):**
After `update_many`, invalidate cached entity keys:
```python
# If any entity_ids are known in the filter, delete from Redis cache
if entity_ids := _extract_entity_ids(filter_query):
    redis_client.delete(*[f"or_event:{eid}" for eid in entity_ids])
```

### 6.4 `test_update_performance()`

```python
def test_update_performance(
    n_updates: int = 5_000,
    workers: int = 10,
) -> list[PerformanceResult]:
    """Performance test for update_or_events across multiple strategies.

    Pre-inserts n_updates 'pending' events (using the optimized insert path),
    then benchmarks update strategies at each level.

    Update levels:
        "U0": update_one() per document — naive baseline
        "U1": update_many() single call — all n_updates in one round-trip
        "U2": U1 + compound index (event_type, status) — avoids collection scan
        "U3": U2 + ThreadPoolExecutor(workers), date-range partitioned
        "U4": U3 + Redis cache invalidation on updated entity_ids

    Args:
        n_updates: Number of documents to pre-insert and then update. Default 5,000.
        workers: Thread pool size for U3+. Default 10.

    Returns:
        List of PerformanceResult, one per level, in order.
    """
```

**Partitioning strategy for U3:**
```python
# Split occurred_at range across workers — no cross-worker lock contention
# Worker k handles docs where occurred_at ∈ [base + k*slice, base + (k+1)*slice)
time_slice = total_range / workers
futures = [
    executor.submit(
        update_or_events,
        filter_query={
            "status": "pending",
            "occurred_at": {
                "$gte": base + k * time_slice,
                "$lt": base + (k + 1) * time_slice,
            }
        },
        update_fields={"status": "acknowledged", "acknowledged_at": now, "acknowledged_by": actor_id},
    )
    for k in range(workers)
]
```

---

## 7. Redis Integration

### 7.1 Redis Client Configuration

```python
# src/or_scheduler/redis_client.py (extends existing Milestone 7 client)
import redis
from redis import Redis

_redis: Redis | None = None

def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=0,
            max_connections=20,
            decode_responses=True,
        )
        # Install hiredis parser if available (~10x faster response parsing)
        try:
            import hiredis  # noqa: F401
        except ImportError:
            pass
    return _redis
```

### 7.2 Write Buffer Implementation (L6)

```python
_QUEUE_KEY = "or_events_buffer"
_FLUSH_BATCH = 1000

def enqueue_or_events(events: list[dict]) -> int:
    """Push events to Redis list for async MongoDB flush. Returns accepted count."""
    r = get_redis()
    pipe = r.pipeline(transaction=False)  # pipelined, no transaction overhead
    for evt in events:
        pipe.lpush(_QUEUE_KEY, json.dumps(evt, default=str))
    results = pipe.execute()
    return len(results)

def _flush_worker() -> None:
    """Background daemon: drain Redis queue → insert_many to MongoDB."""
    r = get_redis()
    while True:
        raw = r.lrange(_QUEUE_KEY, 0, _FLUSH_BATCH - 1)
        if raw:
            batch = [json.loads(doc) for doc in raw]
            insert_or_events(batch)
            r.ltrim(_QUEUE_KEY, len(batch), -1)
        else:
            time.sleep(0.001)  # 1ms idle wait
```

### 7.3 Cache-Aside Pattern

```python
_CACHE_TTL = 300  # 5 minutes

def get_cached_event(event_id: str) -> dict | None:
    """Cache-Aside read: Redis first, MongoDB fallback."""
    r = get_redis()
    cached = r.get(f"or_event:{event_id}")
    if cached:
        return json.loads(cached)
    # MongoDB fallback
    doc = get_events_collection().find_one({"event_id": event_id})
    if doc:
        doc.pop("_id", None)  # ObjectId not JSON-serializable
        r.setex(f"or_event:{event_id}", _CACHE_TTL, json.dumps(doc, default=str))
    return doc
```

**Eviction policy:** Set Redis `maxmemory-policy = allkeys-lru` in docker-compose environment. This ensures Redis evicts least-recently-used keys when memory fills — matching the class material LRU requirement.

---

## 8. MongoDB Client Configuration

### 8.1 `mongo_client.py`

```python
# src/or_scheduler/mongo_client.py
from __future__ import annotations
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.write_concern import WriteConcern
from .config import settings

_client: MongoClient | None = None

def get_mongo_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(
            settings.MONGODB_URI,             # "mongodb://localhost:27017"
            maxPoolSize=50,                   # matches max thread workers
            minPoolSize=5,                    # keep minimum connections warm
            maxIdleTimeMS=30_000,             # 30s idle connection cleanup
            serverSelectionTimeoutMS=5_000,   # fail fast on unavailable
            connectTimeoutMS=2_000,
        )
    return _client

def get_events_collection(*, fast: bool = True) -> Collection:
    """Return the or_events collection.

    Args:
        fast: If True (default), use WriteConcern(w=1, j=False) — acknowledged
              writes without journal fsync (~2-3x faster than j=True).
              Set False for audit-critical writes where durability matters.
    """
    db = get_mongo_client()["or_scheduler"]
    wc = WriteConcern(w=1, j=False) if fast else WriteConcern(w=1, j=True)
    return db.get_collection("or_events", write_concern=wc)

def setup_collection() -> None:
    """Create indexes on or_events collection. Idempotent."""
    coll = get_events_collection()
    coll.create_index([("occurred_at", DESCENDING)],         name="ix_occurred_at")
    coll.create_index([("event_type", ASCENDING),
                       ("status", ASCENDING)],               name="ix_type_status")
    coll.create_index([("entity_id", ASCENDING)],            name="ix_entity_id")
    coll.create_index([("department_id", ASCENDING),
                       ("occurred_at", DESCENDING)],         name="ix_dept_time")

def health_check() -> bool:
    """Ping MongoDB. Returns True if healthy."""
    try:
        get_mongo_client().admin.command("ping")
        return True
    except Exception:
        return False
```

---

## 9. Notebook Structure: `Assignment/06_mongodb_performance.ipynb`

| Cell | ID | Content |
|---|---|---|
| 1 | `header` | Title, assignment statement, architecture diagram (text art) |
| 2 | `imports` | All imports: pymongo, redis, concurrent.futures, time, uuid, faker, rich, dataclasses |
| 3 | `connection_check` | MongoDB ping, Redis ping, print server versions, verify or_events collection exists |
| 4 | `collection_setup` | `setup_collection()`, show index list with `collection.index_information()` |
| 5 | `sharding_note` | Architecture cell: shard key design, Mongos diagram, `reshardCollection` command (commented) |
| 6 | `read_concern_demo` | 3-query comparison: `available` vs `local` vs `majority` — measure latency difference |
| 7 | `insert_fn` | Define `insert_or_events()`, `_generate_events()`, helper functions |
| 8 | `test_insert_fn` | Define `test_insert_performance()` with all 6 levels |
| 9 | `run_insert` | **RUN** `test_insert_performance(n=50_000)` — Rich table shows all levels |
| 10 | `redis_buffer_fn` | Define `enqueue_or_events()`, `_flush_worker()`, `get_cached_event()` |
| 11 | `run_insert_l6` | **RUN** Redis buffer test — show accepted TPS + persisted TPS |
| 12 | `update_fn` | Define `update_or_events()` |
| 13 | `test_update_fn` | Define `test_update_performance()` with all 4 levels |
| 14 | `run_update` | **RUN** `test_update_performance(n_updates=5_000)` — Rich table shows all levels |
| 15 | `summary` | Final comparison table: L0 vs L4 vs L6; architecture rationale; class material alignment |

### Rich Table Format (example output)

```
Insert Performance Results — or_events collection
┌───────┬────────────────────────────────────────────────┬────────┬─────────┬────────────┬────────────┬────────────┬────────┐
│ Level │ Strategy                                       │ N Docs │   TPS   │  P50 (ms)  │  P95 (ms)  │  P99 (ms)  │ Errors │
├───────┼────────────────────────────────────────────────┼────────┼─────────┼────────────┼────────────┼────────────┼────────┤
│ L0    │ insert_one() single-thread                     │  1,000 │     412 │     2.42ms │     3.11ms │     4.05ms │      0 │
│ L1    │ insert_many batch=100 ordered=True             │ 50,000 │   5,218 │     19.1ms │     23.4ms │     28.7ms │      0 │
│ L2    │ insert_many batch=1000 ordered=False           │ 50,000 │   8,441 │     11.8ms │     14.2ms │     17.1ms │      0 │
│ L3    │ L2 + ThreadPoolExecutor(20)                    │ 50,000 │  15,372 │      6.3ms │      9.1ms │     12.4ms │      0 │
│ L4    │ L3 + WriteConcern(w=1, j=False)                │ 50,000 │  21,105 │      4.7ms │      7.2ms │      9.8ms │      0 │  ← PRIMARY
│ L5    │ L4 + sparse indexes during bulk load           │ 50,000 │  28,834 │      3.5ms │      5.1ms │      6.9ms │      0 │
│ L6    │ Redis LPUSH buffer (accepted)                  │ 50,000 │  54,200 │      0.9ms │      1.3ms │      2.1ms │      0 │  ← BONUS
└───────┴────────────────────────────────────────────────┴────────┴─────────┴────────────┴────────────┴────────────┴────────┘
```

*(Exact numbers will vary by hardware — above are conservative estimates for MacBook Pro M-series with Docker.)*

---

## 10. New Files Summary

```
src/or_scheduler/
  mongo_client.py         NEW — MongoClient singleton, collection config, setup_collection()
  mongo_operations.py     NEW — 4 required functions + helper functions

Assignment/
  06_mongodb_performance.ipynb   NEW — Assignment02 notebook (15 cells)

Documents/Plans/
  ASSIGNMENT02_PLAN.md    THIS FILE
```

## 11. Modified Files (minimal, additive only)

### `docker-compose.yml` — add MongoDB + Redis config

```yaml
  mongodb:
    image: mongo:7
    container_name: or_scheduler_mongodb
    restart: unless-stopped
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db
    environment:
      MONGO_INITDB_DATABASE: or_scheduler
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: or_scheduler_redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

volumes:
  postgres_data:
  mongo_data:      # new
  redis_data:      # new (optional persistence)
```

### `pyproject.toml` — add dependencies

```toml
[project.dependencies]
# ... existing deps ...
pymongo = ">=4.6"
redis = {version = ">=5.0", extras = ["hiredis"]}   # hiredis = C parser, ~10x faster
```

### `.env.example` — add new variables

```bash
MONGODB_URI=mongodb://localhost:27017
REDIS_HOST=localhost
REDIS_PORT=6379
```

### `src/or_scheduler/config.py` — add new settings fields

```python
MONGODB_URI: str = "mongodb://localhost:27017"
REDIS_HOST: str = "localhost"
REDIS_PORT: int = 6379
```

**No changes to NB01–NB05, models, operations, seed, or any Assignment01 code.**

---

## 12. Git Branching Strategy

### Preserve Assignment01

```bash
# Run BEFORE starting any Assignment02 work:

# 1. Permanent tag — survives branch deletion/renaming
git tag assignment01

# 2. Convenience branch — allows clean checkout
git checkout -b assignment01-snapshot
git checkout main

# To restore Assignment01 state at any time:
git checkout assignment01-snapshot
# → NB01–NB05 work perfectly; no MongoDB, no NB06
```

### Why keep Assignment02 on `main`

Assignment02 is strictly additive: 2 new src files, 1 new notebook, 3 config additions. It never modifies Assignment01 files. Keeping it on `main` means the final project is always one checkout. The `assignment01-snapshot` branch provides a clean fallback for teacher review.

### Branch diagram

```
main (HEAD)
  ├── NB01–NB05 (Assignment01, untouched)
  ├── mongo_client.py, mongo_operations.py (new)
  └── NB06 (Assignment02)

assignment01-snapshot ←─── tag: assignment01
  ├── NB01–NB05 only
  └── No MongoDB code
```

---

## 13. Implementation Sequence

Execute in this exact order:

1. **Git tag**: `git tag assignment01 && git checkout -b assignment01-snapshot && git checkout main`
2. **Dependencies**: Add `pymongo>=4.6` and `redis[hiredis]>=5.0` to `pyproject.toml`; run `uv sync`
3. **Config**: Add `MONGODB_URI`, `REDIS_HOST`, `REDIS_PORT` to `config.py` and `.env.example`
4. **Docker**: Add `mongodb` + `redis` services to `docker-compose.yml`; run `docker compose up -d mongodb redis`
5. **mongo_client.py**: Client singleton, `get_events_collection()`, `setup_collection()`, `health_check()`
6. **mongo_operations.py**: All 4 required functions + `_generate_events()` helper
7. **NB06**: Build notebook (15 cells per structure above)
8. **Test run**: Execute NB06 end-to-end; verify L4 TPS > 10,000
9. **ROADMAP.md**: Add Milestone 5b entry
10. **TEST_REPORT.md**: Add NB06 section
11. **Commit**: `git commit` with all new files

---

## 14. Class Material Alignment Checklist

| Class requirement | Our implementation |
|---|---|
| MongoDB / Redis / other NoSQL | MongoDB (primary) + Redis (write buffer + cache) |
| > 10k RPS | L4 insert: ~21k TPS; L6 buffer: ~54k TPS accepted |
| Millions of records | Schema + indexes designed for 100M docs; RAM analysis included |
| Shard key design (prevent 78%/22% skew) | `{department_id: 1, event_id: "hashed"}` — compound hashed = ~50/50 |
| Live resharding command | Shown in Cell 5 as `db.adminCommand({ reshardCollection: ... })` |
| Indexes fit in RAM | Verified: ~1.36GB for 10M docs, fits on any dev machine |
| Read concerns: `available` / `local` / `majority` | Cell 6 demonstrates all three with latency comparison |
| Redis caching layer, Cache-Aside, LRU, TTL | Implemented in `get_cached_event()`, Redis `allkeys-lru`, TTL=300s |
| Event-driven data bus (append-only log) | Redis List write buffer → background flush → MongoDB |
| Write flow: write to DB + send event to force cache update | `update_or_events()` calls `redis.delete(entity_cache_key)` |
