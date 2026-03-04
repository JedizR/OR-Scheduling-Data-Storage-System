#!/usr/bin/env python
"""
Initialize the OR Scheduling database.

Idempotent — safe to run multiple times.
Creates PostgreSQL extensions, all tables, GIST exclusion constraint,
partial indexes, and updated_at triggers.
"""

import sys
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import text
from or_scheduler.database import engine
from or_scheduler.models import Base  # imports all models via __init__

# ── Extensions ───────────────────────────────────────────────────────────────

EXTENSIONS = [
    'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";',
    "CREATE EXTENSION IF NOT EXISTS btree_gist;",
]

# ── GIST Exclusion Constraint ─────────────────────────────────────────────────
# Prevents room double-booking at the database level, independent of app logic.
# Uses denormalized reservation_start / reservation_end for clean constraint.

GIST_CONSTRAINT = """
ALTER TABLE room_reservations
    DROP CONSTRAINT IF EXISTS no_room_overlap;
ALTER TABLE room_reservations
    ADD CONSTRAINT no_room_overlap
    EXCLUDE USING GIST (
        room_id WITH =,
        tstzrange(reservation_start, reservation_end, '[)') WITH &&
    )
    WHERE (status NOT IN ('RELEASED', 'COMPLETED'));
"""

# ── Partial Indexes ───────────────────────────────────────────────────────────
# These are not expressible in SQLAlchemy model __table_args__ with WHERE clauses
# easily, so we apply them here via DDL.

INDEXES = [
    # Availability query: most frequent read pattern
    """
    CREATE INDEX IF NOT EXISTS idx_appointments_date_status
        ON appointments(scheduled_date, status)
        WHERE status NOT IN ('CANCELLED','BUMPED','COMPLETED');
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_room_res_room_active
        ON room_reservations(room_id)
        WHERE status NOT IN ('RELEASED','COMPLETED');
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_staff_res_staff_active
        ON staff_reservations(staff_id)
        WHERE status NOT IN ('RELEASED','COMPLETED');
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_equip_res_equip_active
        ON equipment_reservations(equipment_id)
        WHERE status NOT IN ('RELEASED','COMPLETED');
    """,
    # Schedule lookups
    "CREATE INDEX IF NOT EXISTS idx_room_sched_lookup ON room_schedules(room_id, date);",
    "CREATE INDEX IF NOT EXISTS idx_staff_sched_lookup ON staff_schedules(staff_id, date);",
    "CREATE INDEX IF NOT EXISTS idx_equip_sched_lookup ON equipment_schedules(equipment_id, date);",
    # Case lookups
    "CREATE INDEX IF NOT EXISTS idx_cases_patient ON cases(patient_id);",
    "CREATE INDEX IF NOT EXISTS idx_cases_dept_status ON cases(department_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_appts_case ON appointments(case_id);",
]

# ── updated_at Trigger ────────────────────────────────────────────────────────

UPDATED_AT_FUNCTION = """
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TABLES_WITH_UPDATED_AT = [
    "departments",
    "staff",
    "rooms",
    "equipment",
    "patients",
    "cases",
    "appointments",
    "room_reservations",
    "staff_reservations",
    "equipment_reservations",
    "room_schedules",
    "staff_schedules",
    "equipment_schedules",
    "overrides",
]


def init_db() -> None:
    print("► Creating PostgreSQL extensions...")
    with engine.connect() as conn:
        for ext in EXTENSIONS:
            conn.execute(text(ext))
        conn.commit()
    print("  ✓ uuid-ossp, btree_gist")

    print("► Creating tables via ORM metadata...")
    Base.metadata.create_all(engine)
    print(f"  ✓ {len(Base.metadata.tables)} tables")

    print("► Applying GIST exclusion constraint...")
    with engine.connect() as conn:
        conn.execute(text(GIST_CONSTRAINT))
        conn.commit()
    print("  ✓ no_room_overlap constraint on room_reservations")

    print("► Creating partial indexes...")
    with engine.connect() as conn:
        for idx_sql in INDEXES:
            conn.execute(text(idx_sql))
        conn.commit()
    print(f"  ✓ {len(INDEXES)} indexes")

    print("► Creating updated_at triggers...")
    with engine.connect() as conn:
        conn.execute(text(UPDATED_AT_FUNCTION))
        for table in TABLES_WITH_UPDATED_AT:
            conn.execute(text(f"""
                DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table};
                CREATE TRIGGER trg_{table}_updated_at
                    BEFORE UPDATE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
            """))
        conn.commit()
    print(f"  ✓ updated_at triggers on {len(TABLES_WITH_UPDATED_AT)} tables")

    print("\n✅ Database initialized successfully.")


if __name__ == "__main__":
    init_db()
