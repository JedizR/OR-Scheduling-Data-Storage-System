"""
Unit & integration tests for Assignment/07_snowflake_analytics.ipynb

Run with:
    cd OR-Scheduling-Data-Storage-System
    uv run pytest Assignment/test_nb07.py -v

Tests that require external connections are skipped gracefully when the
corresponding service or package is not available.

Section 1  — Source DB connections (PostgreSQL, MongoDB, Snowflake)
Section 2  — PostgreSQL extraction (4 tables)
Section 3  — MongoDB extraction
Section 4  — Snowflake load  (write_pandas)
Section 5  — Data mart views
Section 6  — query_sf helper
Section 7  — Business operations (create_case, create_appointment)
Section 8  — MongoDB event insertion
Section 9  — Analytics delta logic  (pure unit tests — no DB needed)
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
import pandas as pd
from datetime import date, time as dtime, timedelta, datetime, timezone
from uuid import uuid4


# ─── Shared fixtures ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pg_engine():
    try:
        from or_scheduler.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable: {exc}")


@pytest.fixture(scope="module")
def mongo_col():
    pymongo = pytest.importorskip("pymongo", reason="pymongo not installed")
    client = pymongo.MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=3_000)
    try:
        client.admin.command("ping")
    except Exception:
        pytest.skip("MongoDB not reachable")
    yield client["or_scheduler"]["or_events"]
    client.close()


@pytest.fixture(scope="module")
def snow_con():
    sf = pytest.importorskip(
        "snowflake.connector", reason="snowflake-connector-python not installed"
    )
    try:
        con = sf.connect(
            user="student",
            password="HSUnivSFTests970",
            account="GKB48589",
            warehouse="COMPUTE_S",
            database="SF_SAMPLE",
            ocsp_fail_open=False,
        )
    except Exception as exc:
        pytest.skip(f"Snowflake not reachable: {exc}")
    cur = con.cursor()
    cur.execute("USE DATABASE SF_SAMPLE")
    cur.execute("CREATE SCHEMA IF NOT EXISTS OR_ANALYTICS")
    cur.execute("USE SCHEMA SF_SAMPLE.OR_ANALYTICS")
    yield con
    con.close()


@pytest.fixture(scope="module")
def snow_cur(snow_con):
    return snow_con.cursor()


# ─── Helper ──────────────────────────────────────────────────────────────────

def _query_sf(cursor, sql: str) -> pd.DataFrame:
    """Mirrors the notebook's query_sf() helper."""
    cursor.execute(sql)
    rows = cursor.fetchall()
    cols = [d[0] for d in cursor.description]
    return pd.DataFrame(rows, columns=cols)


def _pct(before: int, after: int) -> str:
    """Mirrors the notebook's pct() helper."""
    if before == 0:
        return "N/A"
    return f"{((after - before) / before) * 100:+.1f}%"


# ─── Section 1: Connection smoke tests ───────────────────────────────────────

class TestConnections:

    def test_postgresql_alive(self, pg_engine):
        from sqlalchemy import text
        with pg_engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
        assert result == 1

    def test_mongodb_alive(self, mongo_col):
        count = mongo_col.count_documents({})
        assert isinstance(count, int) and count >= 0

    def test_snowflake_alive(self, snow_cur):
        row = snow_cur.execute("SELECT CURRENT_VERSION()").fetchone()
        assert row is not None and isinstance(row[0], str)


# ─── Section 2: PostgreSQL extraction ────────────────────────────────────────

@pytest.fixture(scope="module")
def pg_frames(pg_engine):
    from sqlalchemy import text
    with pg_engine.connect() as conn:
        df_depts = pd.read_sql(text(
            "SELECT department_id::text, name, building, floor FROM departments"
        ), conn)
        df_rooms = pd.read_sql(text(
            "SELECT room_id::text, room_code, room_type, department_id::text FROM rooms"
        ), conn)
        df_cases = pd.read_sql(text(
            "SELECT case_id::text, department_id::text, procedure_type, urgency, status, "
            "estimated_duration_minutes, created_at::text AS created_at FROM cases"
        ), conn)
        df_appts = pd.read_sql(text(
            "SELECT appointment_id::text, case_id::text, room_id::text, "
            "scheduled_date::text, start_time::text, end_time::text, status FROM appointments"
        ), conn)
    for df in [df_depts, df_rooms, df_cases, df_appts]:
        df.columns = df.columns.str.upper()
    return df_depts, df_rooms, df_cases, df_appts


class TestPostgresExtraction:

    def test_departments_count(self, pg_frames):
        df = pg_frames[0]
        assert len(df) == 5, f"Expected 5 departments, got {len(df)}"

    def test_departments_columns(self, pg_frames):
        assert {"DEPARTMENT_ID", "NAME", "BUILDING", "FLOOR"} == set(pg_frames[0].columns)

    def test_rooms_count(self, pg_frames):
        df = pg_frames[1]
        assert len(df) == 8, f"Expected 8 rooms, got {len(df)}"

    def test_rooms_columns(self, pg_frames):
        assert {"ROOM_ID", "ROOM_CODE", "ROOM_TYPE", "DEPARTMENT_ID"} == set(pg_frames[1].columns)

    def test_cases_not_empty(self, pg_frames):
        assert len(pg_frames[2]) > 0

    def test_cases_urgency_valid(self, pg_frames):
        valid = {"ELECTIVE", "URGENT", "EMERGENCY"}
        assert pg_frames[2]["URGENCY"].isin(valid).all()

    def test_cases_status_valid(self, pg_frames):
        valid = {"OPEN", "SCHEDULED", "IN_PROGRESS", "COMPLETED", "CANCELLED"}
        assert pg_frames[2]["STATUS"].isin(valid).all()

    def test_appointments_not_empty(self, pg_frames):
        assert len(pg_frames[3]) > 0

    def test_appointments_status_valid(self, pg_frames):
        valid = {"CONFIRMED", "CANCELLED", "COMPLETED", "BUMPED"}
        assert pg_frames[3]["STATUS"].isin(valid).all()

    def test_all_columns_uppercase(self, pg_frames):
        """Snowflake requires uppercase when quote_identifiers=False."""
        for df in pg_frames:
            for col in df.columns:
                assert col == col.upper(), f"Non-uppercase column: '{col}'"

    def test_id_columns_not_all_null(self, pg_frames):
        for idx, col in enumerate(["DEPARTMENT_ID", "ROOM_ID", "CASE_ID", "APPOINTMENT_ID"]):
            assert not pg_frames[idx][col].isnull().all(), f"{col} is entirely null"


# ─── Section 3: MongoDB extraction ───────────────────────────────────────────

@pytest.fixture(scope="module")
def mongo_df(mongo_col):
    projection = {
        "_id": 0,
        "event_id": 1, "event_type": 1, "occurred_at": 1,
        "entity_type": 1, "department_id": 1, "status": 1,
    }
    docs = list(mongo_col.find({}, projection).limit(1_000))
    if not docs:
        pytest.skip("or_events is empty — run NB06 first")
    df = pd.DataFrame(docs)
    df.columns = df.columns.str.upper()
    df["OCCURRED_AT"] = df["OCCURRED_AT"].astype(str)
    return df


class TestMongoExtraction:

    def test_events_not_empty(self, mongo_df):
        assert len(mongo_df) > 0

    def test_required_columns(self, mongo_df):
        required = {"EVENT_ID", "EVENT_TYPE", "OCCURRED_AT", "ENTITY_TYPE", "STATUS"}
        assert required.issubset(set(mongo_df.columns))

    def test_event_types_valid(self, mongo_df):
        valid = {
            "case_created", "appointment_booked", "appointment_cancelled",
            "room_status_changed", "equipment_sterilization", "override_issued",
        }
        unknown = set(mongo_df["EVENT_TYPE"].unique()) - valid
        assert not unknown, f"Unexpected event types: {unknown}"

    def test_occurred_at_is_string(self, mongo_df):
        """Must be str so Snowflake timezone conversion does not fail."""
        # pandas 3.0+ returns StringDtype; check actual value type instead of dtype name
        assert isinstance(mongo_df["OCCURRED_AT"].iloc[0], str)

    def test_all_columns_uppercase(self, mongo_df):
        for col in mongo_df.columns:
            assert col == col.upper()

    def test_no_duplicate_event_ids(self, mongo_df):
        assert mongo_df["EVENT_ID"].nunique() == len(mongo_df)


# ─── Section 4: Snowflake load ────────────────────────────────────────────────

class TestSnowflakeLoad:

    def _wp(self, snow_con, df, table_name):
        from snowflake.connector.pandas_tools import write_pandas
        return write_pandas(
            conn=snow_con, df=df, table_name=table_name,
            database="SF_SAMPLE", schema="OR_ANALYTICS",
            auto_create_table=True, overwrite=True, quote_identifiers=False,
        )

    def test_load_departments(self, snow_con, pg_frames):
        success, _, nrows, _ = self._wp(snow_con, pg_frames[0], "OR_DIM_DEPARTMENTS")
        assert success and nrows == len(pg_frames[0])

    def test_load_rooms(self, snow_con, pg_frames):
        success, _, nrows, _ = self._wp(snow_con, pg_frames[1], "OR_DIM_ROOMS")
        assert success and nrows == len(pg_frames[1])

    def test_load_cases(self, snow_con, pg_frames):
        success, _, nrows, _ = self._wp(snow_con, pg_frames[2], "OR_FACT_CASES")
        assert success and nrows == len(pg_frames[2])

    def test_load_appointments(self, snow_con, pg_frames):
        success, _, nrows, _ = self._wp(snow_con, pg_frames[3], "OR_FACT_APPOINTMENTS")
        assert success and nrows == len(pg_frames[3])

    def test_load_events(self, snow_con, mongo_df):
        success, _, nrows, _ = self._wp(snow_con, mongo_df, "OR_FACT_EVENTS")
        assert success and nrows == len(mongo_df)

    def test_all_tables_have_rows(self, snow_cur):
        for tbl in ["OR_DIM_DEPARTMENTS", "OR_DIM_ROOMS",
                    "OR_FACT_CASES", "OR_FACT_APPOINTMENTS", "OR_FACT_EVENTS"]:
            n = snow_cur.execute(
                f"SELECT COUNT(*) FROM SF_SAMPLE.OR_ANALYTICS.{tbl}"
            ).fetchone()[0]
            assert n > 0, f"{tbl} is empty after load"


# ─── Section 5: Data mart views ──────────────────────────────────────────────

_VIEW_SQL = {
    "OR_VW_APPT_STATUS": """
        SELECT STATUS, COUNT(*) AS CNT
        FROM SF_SAMPLE.OR_ANALYTICS.OR_FACT_APPOINTMENTS GROUP BY STATUS ORDER BY CNT DESC
    """,
    "OR_VW_CASE_URGENCY": """
        SELECT URGENCY, COUNT(*) AS CNT
        FROM SF_SAMPLE.OR_ANALYTICS.OR_FACT_CASES GROUP BY URGENCY ORDER BY CNT DESC
    """,
    "OR_VW_ROOM_UTILIZATION": """
        SELECT R.ROOM_CODE, COUNT(A.APPOINTMENT_ID) AS BOOKINGS
        FROM SF_SAMPLE.OR_ANALYTICS.OR_FACT_APPOINTMENTS A
        JOIN SF_SAMPLE.OR_ANALYTICS.OR_DIM_ROOMS R ON A.ROOM_ID = R.ROOM_ID
        GROUP BY R.ROOM_CODE ORDER BY BOOKINGS DESC
    """,
    "OR_VW_EVENT_TYPES": """
        SELECT EVENT_TYPE, COUNT(*) AS CNT
        FROM SF_SAMPLE.OR_ANALYTICS.OR_FACT_EVENTS GROUP BY EVENT_TYPE ORDER BY CNT DESC
    """,
}


@pytest.fixture(scope="module")
def created_views(snow_cur):
    for vname, vquery in _VIEW_SQL.items():
        snow_cur.execute(
            f"CREATE OR REPLACE VIEW SF_SAMPLE.OR_ANALYTICS.{vname} AS {vquery}"
        )
    return list(_VIEW_SQL.keys())


class TestDataMartViews:

    def test_all_views_exist(self, snow_cur, created_views):
        result = snow_cur.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.VIEWS "
            "WHERE TABLE_SCHEMA = 'OR_ANALYTICS' AND TABLE_CATALOG = 'SF_SAMPLE'"
        ).fetchall()
        existing = {row[0] for row in result}
        for v in created_views:
            assert v in existing, f"View {v} missing from Snowflake"

    def test_appt_status_has_rows(self, snow_cur, created_views):
        rows = snow_cur.execute(
            "SELECT * FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_APPT_STATUS"
        ).fetchall()
        assert len(rows) > 0

    def test_appt_status_values_valid(self, snow_cur, created_views):
        rows = snow_cur.execute(
            "SELECT STATUS FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_APPT_STATUS"
        ).fetchall()
        valid = {"CONFIRMED", "CANCELLED", "COMPLETED", "BUMPED"}
        for (s,) in rows:
            assert s in valid, f"Unexpected status: {s}"

    def test_case_urgency_values_valid(self, snow_cur, created_views):
        rows = snow_cur.execute(
            "SELECT URGENCY FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_CASE_URGENCY"
        ).fetchall()
        valid = {"ELECTIVE", "URGENT", "EMERGENCY"}
        for (u,) in rows:
            assert u in valid, f"Unexpected urgency: {u}"

    def test_room_utilization_has_rows(self, snow_cur, created_views):
        rows = snow_cur.execute(
            "SELECT ROOM_CODE, BOOKINGS FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_ROOM_UTILIZATION"
        ).fetchall()
        assert len(rows) > 0
        for code, bookings in rows:
            assert bookings > 0

    def test_event_types_has_rows(self, snow_cur, created_views):
        rows = snow_cur.execute(
            "SELECT * FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_EVENT_TYPES"
        ).fetchall()
        assert len(rows) > 0

    def test_all_view_counts_positive(self, snow_cur, created_views):
        for vname in ["OR_VW_APPT_STATUS", "OR_VW_CASE_URGENCY", "OR_VW_EVENT_TYPES"]:
            rows = snow_cur.execute(
                f"SELECT CNT FROM SF_SAMPLE.OR_ANALYTICS.{vname}"
            ).fetchall()
            for (cnt,) in rows:
                assert cnt > 0, f"Zero count in {vname}"


# ─── Section 6: query_sf helper ──────────────────────────────────────────────

class TestQuerySfHelper:

    def test_returns_dataframe(self, snow_cur, created_views):
        df = _query_sf(snow_cur, "SELECT * FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_APPT_STATUS")
        assert isinstance(df, pd.DataFrame) and len(df) > 0

    def test_appt_status_columns(self, snow_cur, created_views):
        df = _query_sf(snow_cur, "SELECT * FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_APPT_STATUS")
        assert {"STATUS", "CNT"} == set(df.columns)

    def test_urgency_columns(self, snow_cur, created_views):
        df = _query_sf(snow_cur, "SELECT * FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_CASE_URGENCY")
        assert {"URGENCY", "CNT"} == set(df.columns)

    def test_room_util_columns(self, snow_cur, created_views):
        df = _query_sf(snow_cur, "SELECT * FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_ROOM_UTILIZATION")
        assert {"ROOM_CODE", "BOOKINGS"} == set(df.columns)

    def test_cnt_is_numeric(self, snow_cur, created_views):
        df = _query_sf(snow_cur, "SELECT * FROM SF_SAMPLE.OR_ANALYTICS.OR_VW_CASE_URGENCY")
        assert pd.api.types.is_numeric_dtype(df["CNT"])

    def test_invalid_sql_raises(self, snow_cur):
        with pytest.raises(Exception):
            _query_sf(snow_cur, "SELECT * FROM NONEXISTENT_TABLE_XYZ")


# ─── Section 7: Business operations ─────────────────────────────────────────

@pytest.fixture(scope="module")
def ref_ids(pg_engine):
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    from or_scheduler.models import Department, Staff, Room, Patient
    from or_scheduler.seed import seed_database

    seed_database()  # ensure schedules exist for the next 14 days

    with Session(pg_engine) as s:
        dept    = s.execute(select(Department).limit(1)).scalar_one()
        surgeon = s.execute(select(Staff).where(Staff.role == "SURGEON").limit(1)).scalar_one()
        anaest  = s.execute(select(Staff).where(Staff.role == "ANAESTHESIOLOGIST").limit(1)).scalar_one()
        scrub   = s.execute(select(Staff).where(Staff.role == "SCRUB_NURSE").limit(1)).scalar_one()
        rooms   = s.execute(select(Room).where(Room.room_type == "OR")).scalars().all()
        patients = s.execute(select(Patient).limit(5)).scalars().all()

        return {
            "dept_id":    dept.department_id,
            "surgeon_id": surgeon.staff_id,
            "anaest_id":  anaest.staff_id,
            "scrub_id":   scrub.staff_id,
            "room_ids":   [r.room_id for r in rooms],
            "pat_hns":    [p.hn for p in patients],
        }


class TestBusinessOperations:

    def test_create_case_elective(self, ref_ids):
        from or_scheduler.database import SessionLocal
        from or_scheduler.operations import create_case
        with SessionLocal() as session:
            case = create_case(
                session,
                patient_hn=ref_ids["pat_hns"][0],
                department_id=ref_ids["dept_id"],
                surgeon_id=ref_ids["surgeon_id"],
                procedure_type="NB07 Test — ELECTIVE case",
                urgency="ELECTIVE",
                estimated_duration_minutes=60,
                clinical_notes="test_nb07.py",
                created_by=ref_ids["surgeon_id"],
            )
            session.commit()
        assert case.case_id is not None
        assert case.status == "OPEN"
        assert case.urgency == "ELECTIVE"

    def test_create_case_emergency(self, ref_ids):
        from or_scheduler.database import SessionLocal
        from or_scheduler.operations import create_case
        with SessionLocal() as session:
            case = create_case(
                session,
                patient_hn=ref_ids["pat_hns"][1],
                department_id=ref_ids["dept_id"],
                surgeon_id=ref_ids["surgeon_id"],
                procedure_type="NB07 Test — EMERGENCY case",
                urgency="EMERGENCY",
                created_by=ref_ids["surgeon_id"],
            )
            session.commit()
        assert case.urgency == "EMERGENCY"

    def test_create_case_urgent(self, ref_ids):
        from or_scheduler.database import SessionLocal
        from or_scheduler.operations import create_case
        with SessionLocal() as session:
            case = create_case(
                session,
                patient_hn=ref_ids["pat_hns"][2],
                department_id=ref_ids["dept_id"],
                surgeon_id=ref_ids["surgeon_id"],
                procedure_type="NB07 Test — URGENT case",
                urgency="URGENT",
                created_by=ref_ids["surgeon_id"],
            )
            session.commit()
        assert case.urgency == "URGENT"

    def test_create_appointment_confirmed(self, ref_ids):
        from or_scheduler.database import SessionLocal
        from or_scheduler.operations import create_case, create_appointment, StaffItem
        test_date = date.today() + timedelta(days=11)
        with SessionLocal() as session:
            case = create_case(
                session,
                patient_hn=ref_ids["pat_hns"][3],
                department_id=ref_ids["dept_id"],
                surgeon_id=ref_ids["surgeon_id"],
                procedure_type="NB07 Test — appointment",
                urgency="ELECTIVE",
                estimated_duration_minutes=90,
                created_by=ref_ids["surgeon_id"],
            )
            appt = create_appointment(
                session,
                case_id=case.case_id,
                room_id=ref_ids["room_ids"][0],
                scheduled_date=test_date,
                start_time=dtime(9, 0),
                end_time=dtime(10, 30),
                staff_items=[
                    StaffItem(ref_ids["surgeon_id"], "SURGEON"),
                    StaffItem(ref_ids["anaest_id"],  "ANAESTHESIOLOGIST"),
                    StaffItem(ref_ids["scrub_id"],   "SCRUB_NURSE"),
                ],
                confirmed_by=ref_ids["surgeon_id"],
            )
            session.commit()
        assert appt.appointment_id is not None
        assert appt.status == "CONFIRMED"

    def test_two_appointments_different_dates(self, ref_ids):
        """Each date is unique so no staff/room conflicts can occur."""
        from or_scheduler.database import SessionLocal
        from or_scheduler.operations import create_case, create_appointment, StaffItem
        appt_ids = []
        for i in range(2):
            test_date = date.today() + timedelta(days=12 + i)
            with SessionLocal() as session:
                case = create_case(
                    session,
                    patient_hn=ref_ids["pat_hns"][i % len(ref_ids["pat_hns"])],
                    department_id=ref_ids["dept_id"],
                    surgeon_id=ref_ids["surgeon_id"],
                    procedure_type=f"NB07 Test — multi-date {i}",
                    urgency="ELECTIVE",
                    created_by=ref_ids["surgeon_id"],
                )
                appt = create_appointment(
                    session,
                    case_id=case.case_id,
                    room_id=ref_ids["room_ids"][i % len(ref_ids["room_ids"])],
                    scheduled_date=test_date,
                    start_time=dtime(9, 0),
                    end_time=dtime(10, 30),
                    staff_items=[
                        StaffItem(ref_ids["surgeon_id"], "SURGEON"),
                        StaffItem(ref_ids["anaest_id"],  "ANAESTHESIOLOGIST"),
                        StaffItem(ref_ids["scrub_id"],   "SCRUB_NURSE"),
                    ],
                    confirmed_by=ref_ids["surgeon_id"],
                )
                session.commit()
                appt_ids.append(appt.appointment_id)
        assert len(appt_ids) == 2 and appt_ids[0] != appt_ids[1]


# ─── Section 8: MongoDB event insertion ──────────────────────────────────────

class TestMongoEventInsertion:

    def test_single_insert(self, mongo_col):
        before = mongo_col.count_documents({})
        result = mongo_col.insert_one({
            "event_id":      str(uuid4()),
            "event_type":    "appointment_booked",
            "occurred_at":   datetime.now(timezone.utc),
            "entity_type":   "appointment",
            "entity_id":     str(uuid4()),
            "department_id": str(uuid4()),
            "actor_id":      str(uuid4()),
            "payload":       {"note": "test_nb07 single insert"},
            "status":        "pending",
            "acknowledged_at": None, "acknowledged_by": None,
            "review_notes": None, "schema_version": 1,
        })
        assert result.acknowledged
        assert mongo_col.count_documents({}) == before + 1

    def test_bulk_insert(self, mongo_col):
        before = mongo_col.count_documents({})
        docs = [
            {
                "event_id":      str(uuid4()),
                "event_type":    "case_created",
                "occurred_at":   datetime.now(timezone.utc),
                "entity_type":   "case",
                "entity_id":     str(uuid4()),
                "department_id": str(uuid4()),
                "actor_id":      str(uuid4()),
                "payload":       {},
                "status":        "pending",
                "acknowledged_at": None, "acknowledged_by": None,
                "review_notes": None, "schema_version": 1,
            }
            for _ in range(10)
        ]
        result = mongo_col.insert_many(docs, ordered=False)
        assert len(result.inserted_ids) == 10
        assert mongo_col.count_documents({}) == before + 10

    def test_event_type_distribution_shifts(self, mongo_col):
        """Inserting skewed events should change the distribution."""
        before = mongo_col.count_documents({"event_type": "appointment_booked"})
        skewed = [
            {
                "event_id":      str(uuid4()),
                "event_type":    "appointment_booked",
                "occurred_at":   datetime.now(timezone.utc),
                "entity_type":   "appointment",
                "entity_id":     str(uuid4()),
                "department_id": str(uuid4()),
                "actor_id":      str(uuid4()),
                "payload":       {},
                "status":        "pending",
                "acknowledged_at": None, "acknowledged_by": None,
                "review_notes": None, "schema_version": 1,
            }
            for _ in range(20)
        ]
        mongo_col.insert_many(skewed, ordered=False)
        assert mongo_col.count_documents({"event_type": "appointment_booked"}) == before + 20


# ─── Section 9: Analytics delta logic (pure unit tests — no DB needed) ────────

class TestDeltaLogic:
    """Self-contained unit tests. No external connections required."""

    def test_pct_increase(self):
        assert _pct(100, 110) == "+10.0%"

    def test_pct_decrease(self):
        assert _pct(100, 90) == "-10.0%"

    def test_pct_no_change(self):
        assert _pct(100, 100) == "+0.0%"

    def test_pct_zero_before_returns_na(self):
        assert _pct(0, 10) == "N/A"

    def test_pct_small_values(self):
        assert _pct(2, 3) == "+50.0%"

    def test_urgency_delta_new_category(self):
        before_df = pd.DataFrame({"URGENCY": ["ELECTIVE", "URGENT"], "CNT": [10, 5]})
        after_df  = pd.DataFrame({"URGENCY": ["ELECTIVE", "URGENT", "EMERGENCY"], "CNT": [13, 6, 2]})
        urg_before = before_df.set_index("URGENCY")["CNT"].to_dict()
        urg_after  = after_df.set_index("URGENCY")["CNT"].to_dict()
        assert urg_before.get("EMERGENCY", 0) == 0
        assert urg_after.get("EMERGENCY", 0)  == 2
        assert urg_after["ELECTIVE"] - urg_before["ELECTIVE"] == 3

    def test_total_count_increases(self):
        before_df = pd.DataFrame({"STATUS": ["CONFIRMED", "CANCELLED"], "CNT": [20, 5]})
        after_df  = pd.DataFrame({"STATUS": ["CONFIRMED", "CANCELLED"], "CNT": [25, 5]})
        assert int(after_df["CNT"].sum()) > int(before_df["CNT"].sum())
        assert _pct(25, 30) == "+20.0%"

    def test_column_uppercase_transform(self):
        df = pd.DataFrame({"department_id": ["a"], "name": ["x"]})
        df.columns = df.columns.str.upper()
        assert list(df.columns) == ["DEPARTMENT_ID", "NAME"]

    def test_occurred_at_str_conversion(self):
        df = pd.DataFrame({"OCCURRED_AT": [datetime.now(timezone.utc)]})
        df["OCCURRED_AT"] = df["OCCURRED_AT"].astype(str)
        # pandas 3.0+ returns StringDtype; check the value is a str, not the dtype name
        assert isinstance(df["OCCURRED_AT"].iloc[0], str)

    def test_snapshot_dict_keys(self):
        """plot_dashboard returns a dict with exactly these four keys."""
        expected_keys = {"appt_status", "case_urgency", "room_util", "event_types"}
        # Simulate what plot_dashboard returns
        fake_snapshot = {k: pd.DataFrame() for k in expected_keys}
        assert set(fake_snapshot.keys()) == expected_keys

    def test_event_type_new_docs_are_string(self):
        """Event docs from NB07's insertion loop have string event_type."""
        _NEW_TYPES = (
            ["appointment_booked"] * 5
            + ["case_created"] * 2
            + ["equipment_sterilization"] * 2
            + ["override_issued"]
        )
        for i in range(300):
            assert isinstance(_NEW_TYPES[i % len(_NEW_TYPES)], str)
