"""
Seed the OR Scheduling database with realistic starting data.

Idempotent: re-running does not duplicate records (checks by unique keys).
Returns a dict with row counts per entity type.
"""

import random
import uuid
from datetime import date, time, timedelta

from faker import Faker
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import (
    Department,
    Equipment,
    EquipmentSchedule,
    Patient,
    Room,
    RoomSchedule,
    Staff,
    StaffSchedule,
)

faker = Faker("th_TH")
faker_en = Faker("en_US")
random.seed(42)

# ── Constants ─────────────────────────────────────────────────────────────────

DEPARTMENTS = [
    {"name": "ศัลยกรรมกระดูก", "building": "Building A", "floor": 3},
    {"name": "ศัลยกรรมหัวใจ", "building": "Building B", "floor": 4},
    {"name": "ประสาทศัลยศาสตร์", "building": "Building A", "floor": 5},
    {"name": "ศัลยกรรมทั่วไป", "building": "Building C", "floor": 2},
    {"name": "วิสัญญีวิทยา", "building": "Building A", "floor": 3},
]

ROOMS = [
    {"room_code": "OR-1", "room_type": "OR", "dept_idx": 0},
    {"room_code": "OR-2", "room_type": "OR", "dept_idx": 0},
    {"room_code": "OR-3", "room_type": "OR", "dept_idx": 1},
    {"room_code": "OR-4", "room_type": "OR", "dept_idx": 1},
    {"room_code": "OR-5", "room_type": "OR", "dept_idx": 2},
    {"room_code": "OR-6", "room_type": "OR", "dept_idx": 3},
    {"room_code": "HYBRID-1", "room_type": "HYBRID", "dept_idx": 1},
    {"room_code": "ER-1", "room_type": "EMERGENCY", "dept_idx": 3},
]

EQUIPMENT_DATA = [
    {"serial_number": "CARM-001", "equipment_type": "C-arm Fluoroscopy", "sterilization_duration_min": 30},
    {"serial_number": "CARM-002", "equipment_type": "C-arm Fluoroscopy", "sterilization_duration_min": 30},
    {"serial_number": "LAPC-001", "equipment_type": "Laparoscopic Tower", "sterilization_duration_min": 45},
    {"serial_number": "LAPC-002", "equipment_type": "Laparoscopic Tower", "sterilization_duration_min": 45},
    {"serial_number": "DAVINCI-001", "equipment_type": "Robotic Surgical System da Vinci", "sterilization_duration_min": 60},
    {"serial_number": "CELLSAVER-001", "equipment_type": "Cell Saver", "sterilization_duration_min": 20},
]

BLOOD_TYPES = ["A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"]

PROCEDURE_TYPES = [
    "Total Knee Replacement",
    "Laparoscopic Cholecystectomy",
    "Coronary Artery Bypass Graft",
    "Craniotomy for Tumor Resection",
    "Appendectomy",
    "Hip Arthroplasty",
    "Aortic Valve Replacement",
    "Lumbar Discectomy",
    "Colectomy",
    "Thyroidectomy",
]


def _get_or_create_departments(session: Session) -> list[Department]:
    depts = []
    for d in DEPARTMENTS:
        existing = session.execute(
            select(Department).where(Department.name == d["name"])
        ).scalar_one_or_none()
        if existing:
            depts.append(existing)
        else:
            dept = Department(**d)
            session.add(dept)
            session.flush()
            depts.append(dept)
    return depts


def _get_or_create_rooms(session: Session, depts: list[Department]) -> list[Room]:
    rooms = []
    for r in ROOMS:
        existing = session.execute(
            select(Room).where(Room.room_code == r["room_code"])
        ).scalar_one_or_none()
        if existing:
            rooms.append(existing)
        else:
            room = Room(
                room_code=r["room_code"],
                room_type=r["room_type"],
                department_id=depts[r["dept_idx"]].department_id,
                is_laminar_flow=(r["room_type"] in ("OR", "HYBRID")),
                is_active=True,
            )
            session.add(room)
            session.flush()
            rooms.append(room)
    return rooms


def _get_or_create_equipment(session: Session) -> list[Equipment]:
    equips = []
    for e in EQUIPMENT_DATA:
        existing = session.execute(
            select(Equipment).where(Equipment.serial_number == e["serial_number"])
        ).scalar_one_or_none()
        if existing:
            equips.append(existing)
        else:
            equip = Equipment(**e, status="AVAILABLE")
            session.add(equip)
            session.flush()
            equips.append(equip)
    return equips


def _get_or_create_staff(session: Session, depts: list[Department]) -> list[Staff]:
    """
    Creates 20 staff members:
      - 4 Surgeons (1 per surgical dept, excluding Anaesthesiology)
      - 1 Surgeon in General Surgery (extra)
      - 5 Anaesthesiologists (all in Anaesthesiology dept)
      - 5 Scrub Nurses (spread across surgical depts)
      - 5 Coordinators (1 per dept)
    """
    staff_list = []
    license_counter = 1000

    def make_staff(name: str, role: str, dept: Department, with_license: bool = True) -> Staff:
        nonlocal license_counter
        license_num = f"MD-{license_counter:05d}" if with_license else None
        license_counter += 1
        existing = session.execute(
            select(Staff).where(Staff.name == name, Staff.role == role)
        ).scalar_one_or_none()
        if existing:
            return existing
        s = Staff(
            name=name,
            role=role,
            department_id=dept.department_id,
            license_number=license_num,
            is_active=True,
        )
        session.add(s)
        session.flush()
        return s

    # Surgeons — 1 per non-anaesthesiology dept + 1 extra in general surgery
    surgical_depts = [depts[0], depts[1], depts[2], depts[3], depts[3]]
    surgeon_names = [
        "นพ.สมชาย วงศ์สุวรรณ",
        "นพ.ประสิทธิ์ รักษ์ไทย",
        "นพ.วิชัย ศรีสมบูรณ์",
        "นพ.อนุชา พงษ์พิทักษ์",
        "นพ.ธีรพงษ์ มณีรัตน์",
    ]
    for name, dept in zip(surgeon_names, surgical_depts):
        staff_list.append(make_staff(name, "SURGEON", dept))

    # Anaesthesiologists — all in dept[4]
    anaest_names = [
        "นพ.กิตติ บุญเสริม",
        "นพ.สุรชาติ แก้วมณี",
        "นพ.ปิยะ ทองสุข",
        "นพ.รัชดา วิไลวรรณ",
        "นพ.ภาสกร ศุภนิมิตร",
    ]
    for name in anaest_names:
        staff_list.append(make_staff(name, "ANAESTHESIOLOGIST", depts[4]))

    # Scrub Nurses — spread across surgical depts
    nurse_depts = [depts[0], depts[1], depts[2], depts[3], depts[0]]
    nurse_names = [
        "พยาบาล สุนิษา เพชรรัตน์",
        "พยาบาล วราภรณ์ ใจดี",
        "พยาบาล นันทนา สุขสมาน",
        "พยาบาล อัมพร ชัยสิทธิ์",
        "พยาบาล ปาริชาต บุญมี",
    ]
    for name, dept in zip(nurse_names, nurse_depts):
        staff_list.append(make_staff(name, "SCRUB_NURSE", dept, with_license=False))

    # Coordinators — 1 per dept
    coord_names = [
        "ประสานงาน ศิริพร ลาภมาก",
        "ประสานงาน มาลี สุดรัก",
        "ประสานงาน สมหญิง พงษ์ศรี",
        "ประสานงาน วันเพ็ญ ใจงาม",
        "ประสานงาน นิภา วิริยะ",
    ]
    for name, dept in zip(coord_names, depts):
        staff_list.append(make_staff(name, "COORDINATOR", dept, with_license=False))

    return staff_list


def _get_or_create_patients(session: Session, count: int = 100) -> list[Patient]:
    """Create patients with Thai-style HN numbers."""
    existing_count = session.execute(
        select(Patient)
    ).scalars().all()

    if len(existing_count) >= count:
        return list(existing_count[:count])

    patients = list(existing_count)
    start_idx = len(existing_count)

    for i in range(start_idx, count):
        hn = f"HN-{(i + 1):08d}"
        # Use faker_en for names to avoid encoding issues in test output
        first = faker_en.first_name()
        last = faker_en.last_name()
        name = f"{first} {last}"
        p = Patient(
            hn=hn,
            hosxp_ref=f"HXP-{(i + 1):08d}",
            name=name,
            age=random.randint(18, 85),
            blood_type=random.choice(BLOOD_TYPES),
            allergies=random.choice([None, None, None, "Penicillin", "Aspirin", "Latex"]),
        )
        session.add(p)
        patients.append(p)

    session.flush()
    return patients


def _create_schedules(
    session: Session,
    rooms: list[Room],
    staff_list: list[Staff],
    equips: list[Equipment],
    days: int = 14,
) -> int:
    """Create REGULAR schedules for all resources over the next `days` days."""
    today = date.today()
    schedule_count = 0
    work_start = time(8, 0)
    work_end = time(17, 0)

    for day_offset in range(days):
        target_date = today + timedelta(days=day_offset)
        is_weekend = target_date.weekday() >= 5  # Saturday=5, Sunday=6

        # Room schedules — every day
        for room in rooms:
            existing = session.execute(
                select(RoomSchedule).where(
                    RoomSchedule.room_id == room.room_id,
                    RoomSchedule.date == target_date,
                )
            ).scalar_one_or_none()
            if not existing:
                session.add(RoomSchedule(
                    room_id=room.room_id,
                    date=target_date,
                    available_from=work_start,
                    available_until=work_end,
                    schedule_type="REGULAR",
                ))
                schedule_count += 1

        # Staff schedules — REGULAR weekdays, ON_CALL weekends
        for s in staff_list:
            existing = session.execute(
                select(StaffSchedule).where(
                    StaffSchedule.staff_id == s.staff_id,
                    StaffSchedule.date == target_date,
                )
            ).scalar_one_or_none()
            if not existing:
                sched_type = "ON_CALL" if is_weekend else "REGULAR"
                session.add(StaffSchedule(
                    staff_id=s.staff_id,
                    date=target_date,
                    available_from=work_start,
                    available_until=work_end,
                    schedule_type=sched_type,
                ))
                schedule_count += 1

        # Equipment schedules — every day
        for equip in equips:
            existing = session.execute(
                select(EquipmentSchedule).where(
                    EquipmentSchedule.equipment_id == equip.equipment_id,
                    EquipmentSchedule.date == target_date,
                )
            ).scalar_one_or_none()
            if not existing:
                session.add(EquipmentSchedule(
                    equipment_id=equip.equipment_id,
                    date=target_date,
                    available_from=work_start,
                    available_until=work_end,
                    schedule_type="REGULAR",
                ))
                schedule_count += 1

    session.flush()
    return schedule_count


def seed_database(session: Session | None = None) -> dict[str, int]:
    """
    Populate the database with realistic seed data.
    Idempotent: safe to call multiple times.

    Returns a dict with counts of created/existing records per entity.
    """
    own_session = session is None
    if own_session:
        session = SessionLocal()

    try:
        print("Seeding departments...")
        depts = _get_or_create_departments(session)
        session.flush()

        print("Seeding rooms...")
        rooms = _get_or_create_rooms(session, depts)
        session.flush()

        print("Seeding equipment...")
        equips = _get_or_create_equipment(session)
        session.flush()

        print("Seeding staff...")
        staff_list = _get_or_create_staff(session, depts)
        session.flush()

        print("Seeding patients...")
        patients = _get_or_create_patients(session, count=100)
        session.flush()

        print("Seeding schedules (14 days)...")
        schedule_count = _create_schedules(session, rooms, staff_list, equips, days=14)

        if own_session:
            session.commit()

        counts = {
            "departments": len(depts),
            "rooms": len(rooms),
            "equipment": len(equips),
            "staff": len(staff_list),
            "patients": len(patients),
            "schedules_created": schedule_count,
        }
        print(f"\n✅ Seed complete: {counts}")
        return counts

    except Exception:
        if own_session:
            session.rollback()
        raise
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    seed_database()
