"""MongoDB client singleton and or_events collection configuration."""
from __future__ import annotations

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.write_concern import WriteConcern

from .config import settings

_client: MongoClient | None = None


def get_mongo_client() -> MongoClient:
    """Return the shared MongoClient instance (created once, thread-safe)."""
    global _client
    if _client is None:
        _client = MongoClient(
            settings.mongodb_uri,
            maxPoolSize=50,          # matches max ThreadPoolExecutor workers
            minPoolSize=5,           # keep minimum connections warm
            maxIdleTimeMS=30_000,    # 30s idle connection cleanup
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=2_000,
        )
    return _client


def get_events_collection(*, fast: bool = True) -> Collection:
    """Return the or_events collection with appropriate write concern.

    Args:
        fast: If True (default), uses WriteConcern(w=1, j=False) —
              acknowledged write without journal fsync, ~2-3x faster
              than j=True. Set False only for audit-critical writes.
    """
    db = get_mongo_client()["or_scheduler"]
    wc = WriteConcern(w=1, j=False) if fast else WriteConcern(w=1, j=True)
    return db.get_collection("or_events", write_concern=wc)


def setup_collection() -> dict:
    """Create indexes on or_events. Idempotent — safe to call multiple times.

    Returns:
        Dict mapping index name to creation result.
    """
    coll = get_events_collection()
    results = {}

    results["ix_occurred_at"] = coll.create_index(
        [("occurred_at", DESCENDING)],
        name="ix_occurred_at",
    )
    results["ix_type_status"] = coll.create_index(
        [("event_type", ASCENDING), ("status", ASCENDING)],
        name="ix_type_status",
    )
    results["ix_entity_id"] = coll.create_index(
        [("entity_id", ASCENDING)],
        name="ix_entity_id",
    )
    results["ix_dept_time"] = coll.create_index(
        [("department_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_dept_time",
    )
    return results


def drop_secondary_indexes() -> None:
    """Drop all indexes except _id (used during bulk load for max insert speed)."""
    coll = get_events_collection()
    for name in ("ix_occurred_at", "ix_type_status", "ix_entity_id", "ix_dept_time"):
        try:
            coll.drop_index(name)
        except Exception:
            pass  # already absent


def health_check() -> bool:
    """Ping MongoDB. Returns True if reachable."""
    try:
        get_mongo_client().admin.command("ping")
        return True
    except Exception:
        return False
