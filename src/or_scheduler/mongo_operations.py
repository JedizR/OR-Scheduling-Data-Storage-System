"""Assignment02 — High-RPS MongoDB OLTP operations.

Four required functions:
    1. insert_or_events()         — bulk-insert OR event documents
    2. test_insert_performance()  — benchmark inserts at 6 optimization levels
    3. update_or_events()         — bulk-update OR events via update_many()
    4. test_update_performance()  — benchmark updates at 4 optimization levels

Architecture: Application → Redis List (write buffer) → MongoDB (source of truth)
              Application → Redis Cache (cache-aside, LRU, TTL=300s) → MongoDB (fallback)
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import redis as redis_lib
from pymongo import ReadPreference
from pymongo.errors import BulkWriteError, ConnectionFailure
from pymongo.read_concern import ReadConcern
from pymongo.results import InsertManyResult, UpdateResult
from pymongo.write_concern import WriteConcern

from .config import settings
from .mongo_client import (
    drop_secondary_indexes,
    get_events_collection,
    get_mongo_client,
    setup_collection,
)

# ---------------------------------------------------------------------------
# Pre-built ID pools — avoids uuid4() in inner loop for non-unique fields
# ---------------------------------------------------------------------------
_DEPT_IDS: list[str] = [str(uuid4()) for _ in range(5)]
_ACTOR_IDS: list[str] = [str(uuid4()) for _ in range(20)]
_EVENT_TYPES: list[str] = [
    "case_created",
    "appointment_booked",
    "appointment_cancelled",
    "room_status_changed",
    "equipment_sterilization",
    "override_issued",
]
_ENTITY_TYPES: list[str] = ["case", "appointment", "appointment", "room", "equipment", "override"]
_STATUSES: list[str] = (["pending"] * 7) + (["acknowledged"] * 2) + ["resolved"]

# Pre-built payload templates (one per event_type)
_PAYLOAD_TEMPLATES: list[dict] = [
    {"patient_id": str(uuid4()), "urgency": "ELECTIVE", "diagnosis_code": "K40.9"},
    {"room_id": str(uuid4()), "scheduled_date": "2026-04-01", "duration_min": 120,
     "surgeon_id": str(uuid4()), "urgency": "ELECTIVE"},
    {"reason": "Patient request", "cancelled_by": str(uuid4()), "original_date": "2026-04-01"},
    {"room_id": str(uuid4()), "old_status": "AVAILABLE", "new_status": "IN_USE"},
    {"equipment_id": str(uuid4()), "sterilization_end": "2026-04-01T12:00:00Z"},
    {"emergency_appt_id": str(uuid4()), "bumped_ids": [str(uuid4())], "override_reason": "Emergency"},
]

# ---------------------------------------------------------------------------
# Redis client (shared, lazy-init, thread-safe)
# ---------------------------------------------------------------------------
_redis_client: redis_lib.Redis | None = None
_redis_lock = threading.Lock()

_QUEUE_KEY = "or_events_buffer"
_CACHE_TTL = 300   # seconds
_FLUSH_BATCH = 1_000


def _get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        with _redis_lock:
            if _redis_client is None:
                _redis_client = redis_lib.Redis(
                    host=settings.redis_host,
                    port=settings.redis_port,
                    db=0,
                    max_connections=20,
                    decode_responses=True,
                    socket_connect_timeout=2,
                )
    return _redis_client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _generate_events(n: int) -> list[dict]:
    """Pre-generate n OR event documents in one vectorized pass.

    Uses pre-built ID pools for department_id, actor_id, and payload
    to avoid redundant uuid4() calls. Only event_id is unique per doc.
    """
    now = datetime.now(timezone.utc)
    n_types = len(_EVENT_TYPES)
    n_statuses = len(_STATUSES)
    return [
        {
            "event_id":      str(uuid4()),               # unique per doc
            "event_type":    _EVENT_TYPES[i % n_types],
            "occurred_at":   now - timedelta(seconds=i),
            "entity_type":   _ENTITY_TYPES[i % n_types],
            "entity_id":     str(uuid4()),               # unique per doc
            "department_id": _DEPT_IDS[i % 5],
            "actor_id":      _ACTOR_IDS[i % 20],
            "payload":       _PAYLOAD_TEMPLATES[i % n_types],
            "status":        _STATUSES[i % n_statuses],
            "acknowledged_at":  None,
            "acknowledged_by":  None,
            "review_notes":     None,
            "schema_version":   1,
        }
        for i in range(n)
    ]


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute percentile p (0-100) from a pre-sorted list."""
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * p / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _chunk(lst: list, size: int):
    """Yield successive chunks of `size` from lst."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class InsertResult:
    inserted_count: int
    acknowledged: bool
    duration_ms: float


@dataclass
class UpdateBatchResult:
    matched_count: int
    modified_count: int
    duration_ms: float


@dataclass
class PerformanceResult:
    level: str
    strategy: str
    n_docs: int
    batch_size: int
    workers: int
    total_time_s: float
    tps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    errors: int


# ===========================================================================
# FUNCTION 1: insert_or_events
# ===========================================================================

def insert_or_events(
    events: list[dict],
    *,
    ordered: bool = False,
    write_concern: Optional[WriteConcern] = None,
) -> InsertResult:
    """Bulk-insert OR event documents into the or_events collection.

    Uses unordered execution by default to allow parallel server-side
    processing. This is the primary insert path: Application → MongoDB.

    Args:
        events: List of event dicts. Each must include at minimum:
                event_id, event_type, occurred_at, entity_type, entity_id.
        ordered: If False (default), server may process in any order for
                 higher throughput. Set True only if insertion order matters.
        write_concern: Override the collection-level write concern.
                       Default: WriteConcern(w=1, j=False).

    Returns:
        InsertResult(inserted_count, acknowledged, duration_ms).

    Raises:
        ValueError: If events is empty.
        BulkWriteError: On partial failure when ordered=True.
        ConnectionFailure: On MongoDB connection loss.
    """
    if not events:
        raise ValueError("events list must not be empty")

    coll = get_events_collection(fast=True)
    if write_concern is not None:
        coll = coll.with_options(write_concern=write_concern)

    t0 = time.perf_counter()
    result: InsertManyResult = coll.insert_many(events, ordered=ordered)
    duration_ms = (time.perf_counter() - t0) * 1_000

    return InsertResult(
        inserted_count=len(result.inserted_ids),
        acknowledged=result.acknowledged,
        duration_ms=duration_ms,
    )


# ===========================================================================
# FUNCTION 2: test_insert_performance
# ===========================================================================

def test_insert_performance(
    n: int = 50_000,
    levels: Optional[list[str]] = None,
) -> list[PerformanceResult]:
    """Benchmark insert_or_events across multiple optimization levels.

    Generates n synthetic OR event documents and runs benchmarks at each
    level, demonstrating the performance improvement at each optimization step.

    Levels (each builds on the previous):
        L0: insert_one() per doc, single thread      — naive baseline
        L1: insert_many batch=100, ordered=True       — basic batching
        L2: insert_many batch=1000, ordered=False     — unordered batching
        L3: L2 + ThreadPoolExecutor(20) workers       — parallelism
        L4: L3 + WriteConcern(w=1, j=False)           — no journal fsync  ← PRIMARY
        L5: L4 + drop secondary indexes during load   — write-optimal schema
        L6: Redis LPUSH write buffer (accepted TPS)   — event-driven bus   ← BONUS

    Args:
        n: Total documents to insert per level. Default 50,000.
           L0 uses min(n, 1000) to keep baseline fast.
        levels: Subset of level names to run. Default: all levels.

    Returns:
        List of PerformanceResult, one per level, in insertion order.
    """
    all_levels = ["L0", "L1", "L2", "L3", "L4", "L5", "L6"]
    if levels is None:
        levels = all_levels
    results: list[PerformanceResult] = []

    # Pre-generate events once — reused (with shallow copy) per level
    events_full = _generate_events(n)
    events_l0   = events_full[: min(n, 1_000)]   # L0 uses smaller set

    for level in levels:
        coll = get_events_collection(fast=True)

        # --- Reset collection between levels ---
        coll.drop()
        setup_collection()

        if level == "L0":
            # ---------------------------------------------------------------
            # L0: insert_one() single thread — naive baseline
            # ---------------------------------------------------------------
            latencies: list[float] = []
            errors = 0
            t_start = time.perf_counter()
            for doc in events_l0:
                try:
                    t0 = time.perf_counter()
                    coll.insert_one(doc)
                    latencies.append((time.perf_counter() - t0) * 1_000)
                except Exception:
                    errors += 1
            total_s = time.perf_counter() - t_start
            latencies.sort()
            results.append(PerformanceResult(
                level="L0", strategy="insert_one() single-thread",
                n_docs=len(events_l0), batch_size=1, workers=1,
                total_time_s=total_s,
                tps=len(events_l0) / total_s,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                errors=errors,
            ))

        elif level == "L1":
            # ---------------------------------------------------------------
            # L1: insert_many batch=100, ordered=True — basic batching
            # ---------------------------------------------------------------
            batch_size = 100
            latencies = []
            errors = 0
            t_start = time.perf_counter()
            for batch in _chunk(events_full, batch_size):
                try:
                    t0 = time.perf_counter()
                    coll.insert_many(batch, ordered=True)
                    latencies.append((time.perf_counter() - t0) * 1_000)
                except BulkWriteError:
                    errors += 1
            total_s = time.perf_counter() - t_start
            latencies.sort()
            results.append(PerformanceResult(
                level="L1", strategy="insert_many batch=100 ordered=True",
                n_docs=n, batch_size=batch_size, workers=1,
                total_time_s=total_s, tps=n / total_s,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                errors=errors,
            ))

        elif level == "L2":
            # ---------------------------------------------------------------
            # L2: insert_many batch=1000, ordered=False — unordered batching
            # ---------------------------------------------------------------
            batch_size = 1_000
            latencies = []
            errors = 0
            t_start = time.perf_counter()
            for batch in _chunk(events_full, batch_size):
                try:
                    t0 = time.perf_counter()
                    coll.insert_many(batch, ordered=False)
                    latencies.append((time.perf_counter() - t0) * 1_000)
                except BulkWriteError:
                    errors += 1
            total_s = time.perf_counter() - t_start
            latencies.sort()
            results.append(PerformanceResult(
                level="L2", strategy="insert_many batch=1000 ordered=False",
                n_docs=n, batch_size=batch_size, workers=1,
                total_time_s=total_s, tps=n / total_s,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                errors=errors,
            ))

        elif level == "L3":
            # ---------------------------------------------------------------
            # L3: L2 + ThreadPoolExecutor(20) — parallel workers
            # ---------------------------------------------------------------
            batch_size = 1_000
            workers = 20
            batches = list(_chunk(events_full, batch_size))
            latencies = []
            errors = 0
            t_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as exe:
                futures = {
                    exe.submit(_timed_insert_many, coll, b, False): b
                    for b in batches
                }
                for fut in as_completed(futures):
                    lat, err = fut.result()
                    latencies.append(lat)
                    errors += err
            total_s = time.perf_counter() - t_start
            latencies.sort()
            results.append(PerformanceResult(
                level="L3", strategy="L2 + ThreadPoolExecutor(20)",
                n_docs=n, batch_size=batch_size, workers=workers,
                total_time_s=total_s, tps=n / total_s,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                errors=errors,
            ))

        elif level == "L4":
            # ---------------------------------------------------------------
            # L4: L3 + WriteConcern(w=1, j=False) — no journal fsync  PRIMARY
            # ---------------------------------------------------------------
            batch_size = 1_000
            workers = 20
            coll_fast = get_events_collection(fast=True)
            batches = list(_chunk(events_full, batch_size))
            latencies = []
            errors = 0
            t_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as exe:
                futures = {
                    exe.submit(_timed_insert_many, coll_fast, b, False): b
                    for b in batches
                }
                for fut in as_completed(futures):
                    lat, err = fut.result()
                    latencies.append(lat)
                    errors += err
            total_s = time.perf_counter() - t_start
            latencies.sort()
            results.append(PerformanceResult(
                level="L4", strategy="L3 + WriteConcern(w=1, j=False)",
                n_docs=n, batch_size=batch_size, workers=workers,
                total_time_s=total_s, tps=n / total_s,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                errors=errors,
            ))

        elif level == "L5":
            # ---------------------------------------------------------------
            # L5: L4 + drop secondary indexes before bulk load
            # ---------------------------------------------------------------
            batch_size = 1_000
            workers = 20
            drop_secondary_indexes()           # only _id index remains
            coll_fast = get_events_collection(fast=True)
            batches = list(_chunk(events_full, batch_size))
            latencies = []
            errors = 0
            t_start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as exe:
                futures = {
                    exe.submit(_timed_insert_many, coll_fast, b, False): b
                    for b in batches
                }
                for fut in as_completed(futures):
                    lat, err = fut.result()
                    latencies.append(lat)
                    errors += err
            total_s = time.perf_counter() - t_start
            setup_collection()                 # recreate indexes after load
            latencies.sort()
            results.append(PerformanceResult(
                level="L5", strategy="L4 + drop secondary indexes during load",
                n_docs=n, batch_size=batch_size, workers=workers,
                total_time_s=total_s, tps=n / total_s,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                errors=errors,
            ))

        elif level == "L6":
            # ---------------------------------------------------------------
            # L6: Redis LPUSH write buffer — measures accepted TPS
            # Collection is NOT reset so L5 data remains as background state
            # ---------------------------------------------------------------
            r = _get_redis()
            r.delete(_QUEUE_KEY)   # clean queue

            # Start background flush worker
            flush_stop = threading.Event()
            flush_thread = threading.Thread(
                target=_redis_flush_worker,
                args=(flush_stop,),
                daemon=True,
            )
            flush_thread.start()

            # Measure how fast we can push to Redis
            latencies = []
            errors = 0
            t_start = time.perf_counter()
            pipe = r.pipeline(transaction=False)
            for i, doc in enumerate(events_full):
                pipe.lpush(_QUEUE_KEY, json.dumps(doc, default=str))
                if (i + 1) % 500 == 0:
                    t0 = time.perf_counter()
                    pipe.execute()
                    latencies.append((time.perf_counter() - t0) * 1_000)
                    pipe = r.pipeline(transaction=False)
            if pipe.command_stack:
                t0 = time.perf_counter()
                pipe.execute()
                latencies.append((time.perf_counter() - t0) * 1_000)
            total_s = time.perf_counter() - t_start

            # Wait for flush worker to drain the queue (max 30s)
            deadline = time.time() + 30
            while r.llen(_QUEUE_KEY) > 0 and time.time() < deadline:
                time.sleep(0.05)
            flush_stop.set()

            latencies.sort()
            results.append(PerformanceResult(
                level="L6", strategy="Redis LPUSH buffer (accepted TPS)",
                n_docs=n, batch_size=500, workers=1,
                total_time_s=total_s, tps=n / total_s,
                p50_ms=_percentile(latencies, 50),
                p95_ms=_percentile(latencies, 95),
                p99_ms=_percentile(latencies, 99),
                errors=errors,
            ))

    return results


def _timed_insert_many(coll, batch: list[dict], ordered: bool) -> tuple[float, int]:
    """Insert a batch and return (latency_ms, error_count). Used by thread workers."""
    try:
        t0 = time.perf_counter()
        coll.insert_many(batch, ordered=ordered)
        return (time.perf_counter() - t0) * 1_000, 0
    except BulkWriteError:
        return 0.0, 1
    except Exception:
        return 0.0, 1


def _redis_flush_worker(stop_event: threading.Event) -> None:
    """Background daemon: drain Redis queue → insert_many to MongoDB."""
    r = _get_redis()
    coll = get_events_collection(fast=True)
    while not stop_event.is_set():
        raw = r.lrange(_QUEUE_KEY, 0, _FLUSH_BATCH - 1)
        if raw:
            try:
                docs = [json.loads(d) for d in raw]
                coll.insert_many(docs, ordered=False)
                r.ltrim(_QUEUE_KEY, len(raw), -1)
            except Exception:
                time.sleep(0.001)
        else:
            time.sleep(0.001)


# ===========================================================================
# FUNCTION 3: update_or_events
# ===========================================================================

def update_or_events(
    filter_query: dict,
    update_fields: dict,
    *,
    upsert: bool = False,
    invalidate_cache: bool = True,
) -> UpdateBatchResult:
    """Bulk-update OR events matching filter_query using update_many().

    Single server round-trip covers all matching documents.
    Optionally invalidates Redis cache keys for updated entities.

    Args:
        filter_query: MongoDB filter. Must include at least one field.
                      For best performance, include an indexed field:
                      event_type, status, entity_id, department_id, occurred_at.
        update_fields: Dict of fields to $set on matched documents.
                       Do NOT include $set operator — added internally.
                       updated_at is always added automatically.
        upsert: Insert a document if no match found. Default False.
        invalidate_cache: Delete Redis cache keys for matched entity_ids.
                          Default True.

    Returns:
        UpdateBatchResult(matched_count, modified_count, duration_ms).

    Raises:
        ValueError: If filter_query is empty (would update entire collection).
        ConnectionFailure: On MongoDB connection loss.
    """
    if not filter_query:
        raise ValueError(
            "filter_query cannot be empty — would update all documents. "
            "Pass an explicit filter or use {'status': {'$exists': True}} to update all."
        )

    update_doc = {"$set": {**update_fields, "updated_at": datetime.now(timezone.utc)}}
    coll = get_events_collection(fast=True)

    t0 = time.perf_counter()
    result: UpdateResult = coll.update_many(filter_query, update_doc, upsert=upsert)
    duration_ms = (time.perf_counter() - t0) * 1_000

    # Cache invalidation — delete any cached event documents for known entity_ids
    if invalidate_cache and (entity_id := filter_query.get("entity_id")):
        try:
            _get_redis().delete(f"or_event:{entity_id}")
        except Exception:
            pass  # Redis down → graceful degradation, cache staleness acceptable

    return UpdateBatchResult(
        matched_count=result.matched_count,
        modified_count=result.modified_count,
        duration_ms=duration_ms,
    )


# ===========================================================================
# FUNCTION 4: test_update_performance
# ===========================================================================

def test_update_performance(
    n_updates: int = 5_000,
    workers: int = 10,
) -> list[PerformanceResult]:
    """Benchmark update_or_events across multiple optimization strategies.

    Pre-inserts n_updates 'pending' events (using the optimized insert path),
    then benchmarks four update strategies in order.

    Update levels:
        U0: update_one() per doc — naive baseline
        U1: update_many() single call — all docs, one round-trip
        U2: U1 + compound index (event_type, status)
        U3: U2 + ThreadPoolExecutor(workers), date-range partitioned

    Args:
        n_updates: Documents to pre-insert and then update. Default 5,000.
        workers: Thread pool size for U3. Default 10.

    Returns:
        List of PerformanceResult, one per level, in order.
    """
    results: list[PerformanceResult] = []
    now = datetime.now(timezone.utc)
    actor_id = str(uuid4())

    # Common update payload applied at each level
    update_payload = {
        "status": "acknowledged",
        "acknowledged_at": now,
        "acknowledged_by": actor_id,
        "review_notes": "Reviewed during performance test",
    }

    def _pre_insert() -> None:
        """Reset collection, seed n_updates pending docs with insert_many."""
        coll = get_events_collection(fast=True)
        coll.drop()
        setup_collection()
        # Generate events all with status='pending' for clean update targets
        docs = [
            {
                "event_id":      str(uuid4()),
                "event_type":    "appointment_booked",
                "occurred_at":   now - timedelta(seconds=i),
                "entity_type":   "appointment",
                "entity_id":     str(uuid4()),
                "department_id": _DEPT_IDS[i % 5],
                "actor_id":      _ACTOR_IDS[i % 20],
                "payload":       _PAYLOAD_TEMPLATES[1],
                "status":        "pending",
                "acknowledged_at":  None,
                "acknowledged_by":  None,
                "review_notes":     None,
                "schema_version":   1,
            }
            for i in range(n_updates)
        ]
        for batch in _chunk(docs, 1_000):
            coll.insert_many(batch, ordered=False)

    # -----------------------------------------------------------------------
    # U0: update_one() per doc — naive baseline
    # -----------------------------------------------------------------------
    _pre_insert()
    coll = get_events_collection(fast=True)
    # Fetch all _ids
    doc_ids = [d["_id"] for d in coll.find({}, {"_id": 1})]

    latencies: list[float] = []
    errors = 0
    t_start = time.perf_counter()
    for doc_id in doc_ids:
        try:
            t0 = time.perf_counter()
            coll.update_one({"_id": doc_id}, {"$set": update_payload})
            latencies.append((time.perf_counter() - t0) * 1_000)
        except Exception:
            errors += 1
    total_s = time.perf_counter() - t_start
    latencies.sort()
    results.append(PerformanceResult(
        level="U0", strategy="update_one() per doc",
        n_docs=n_updates, batch_size=1, workers=1,
        total_time_s=total_s, tps=n_updates / total_s,
        p50_ms=_percentile(latencies, 50),
        p95_ms=_percentile(latencies, 95),
        p99_ms=_percentile(latencies, 99),
        errors=errors,
    ))

    # -----------------------------------------------------------------------
    # U1: update_many() single call — one round-trip
    # -----------------------------------------------------------------------
    _pre_insert()
    coll = get_events_collection(fast=True)
    t0 = time.perf_counter()
    res = coll.update_many({"status": "pending"}, {"$set": update_payload})
    total_s = time.perf_counter() - t0
    results.append(PerformanceResult(
        level="U1", strategy="update_many() single call",
        n_docs=res.modified_count, batch_size=n_updates, workers=1,
        total_time_s=total_s, tps=res.modified_count / max(total_s, 1e-9),
        p50_ms=total_s * 1_000,
        p95_ms=total_s * 1_000,
        p99_ms=total_s * 1_000,
        errors=0,
    ))

    # -----------------------------------------------------------------------
    # U2: U1 + compound index on (event_type, status) — avoids collection scan
    # -----------------------------------------------------------------------
    _pre_insert()
    coll = get_events_collection(fast=True)
    # ix_type_status already created by setup_collection — just measure
    t0 = time.perf_counter()
    res = coll.update_many(
        {"event_type": "appointment_booked", "status": "pending"},
        {"$set": update_payload},
    )
    total_s = time.perf_counter() - t0
    results.append(PerformanceResult(
        level="U2", strategy="update_many + compound index (event_type, status)",
        n_docs=res.modified_count, batch_size=n_updates, workers=1,
        total_time_s=total_s, tps=res.modified_count / max(total_s, 1e-9),
        p50_ms=total_s * 1_000,
        p95_ms=total_s * 1_000,
        p99_ms=total_s * 1_000,
        errors=0,
    ))

    # -----------------------------------------------------------------------
    # U3: U2 + ThreadPoolExecutor, date-range partitioned
    # -----------------------------------------------------------------------
    _pre_insert()
    coll = get_events_collection(fast=True)
    # Partition occurred_at range evenly across workers — no lock contention
    total_range = timedelta(seconds=n_updates)
    slice_s = total_range / workers
    base = now - total_range

    latencies = []
    errors = 0
    t_start = time.perf_counter()

    def _range_update(k: int) -> tuple[float, int, int]:
        start = base + k * slice_s
        end   = base + (k + 1) * slice_s
        try:
            t0 = time.perf_counter()
            r = coll.update_many(
                {
                    "event_type": "appointment_booked",
                    "status": "pending",
                    "occurred_at": {"$gte": start, "$lt": end},
                },
                {"$set": update_payload},
            )
            return (time.perf_counter() - t0) * 1_000, r.modified_count, 0
        except Exception:
            return 0.0, 0, 1

    with ThreadPoolExecutor(max_workers=workers) as exe:
        futs = [exe.submit(_range_update, k) for k in range(workers)]
        total_modified = 0
        for fut in as_completed(futs):
            lat, modified, err = fut.result()
            latencies.append(lat)
            total_modified += modified
            errors += err

    total_s = time.perf_counter() - t_start
    latencies.sort()
    results.append(PerformanceResult(
        level="U3", strategy=f"U2 + ThreadPoolExecutor({workers}), date-range partitioned",
        n_docs=total_modified, batch_size=n_updates // workers, workers=workers,
        total_time_s=total_s, tps=total_modified / max(total_s, 1e-9),
        p50_ms=_percentile(latencies, 50),
        p95_ms=_percentile(latencies, 95),
        p99_ms=_percentile(latencies, 99),
        errors=errors,
    ))

    return results


# ===========================================================================
# Cache-Aside read helper (bonus — demonstrates Redis caching layer)
# ===========================================================================

def get_cached_event(event_id: str) -> Optional[dict]:
    """Cache-Aside read: Redis first, MongoDB fallback.

    On cache miss, fetches from MongoDB and caches with TTL=300s.
    On Redis failure, falls back to MongoDB gracefully.

    Args:
        event_id: The event_id field value (UUID string) to look up.

    Returns:
        Event document dict (without _id), or None if not found.
    """
    cache_key = f"or_event:{event_id}"
    try:
        r = _get_redis()
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass  # Redis unavailable — fall through to MongoDB

    doc = get_events_collection().find_one({"event_id": event_id})
    if doc:
        doc.pop("_id", None)   # ObjectId is not JSON-serializable
        try:
            _get_redis().setex(cache_key, _CACHE_TTL, json.dumps(doc, default=str))
        except Exception:
            pass  # cache write failure is non-fatal
    return doc


def enqueue_or_events(events: list[dict]) -> int:
    """Push events to Redis write buffer for async MongoDB flush.

    This is the L6 write path: Application → Redis List → MongoDB.
    Returns the number of events accepted (LPUSH count).

    Args:
        events: List of event dicts to enqueue.

    Returns:
        Number of events pushed to the Redis list.
    """
    r = _get_redis()
    pipe = r.pipeline(transaction=False)
    for doc in events:
        pipe.lpush(_QUEUE_KEY, json.dumps(doc, default=str))
    results = pipe.execute()
    return len(results)
