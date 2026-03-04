# OLTP Database Assignment Requirements

These are the requirements for the assignment that must be completed and presented to the teacher.

## 1 & 2. Database Schema & ORM Setup

The teacher requires the Mermaid ER diagram to be translated into Python code using an Object-Relational Mapper (ORM) like SQLAlchemy or Django ORM.

**What you actually code:**

* Python classes for the entities (e.g., `class Patient`, `class Room`, `class Appointment`).
* Column definitions, **Primary Keys**, and **Foreign Keys** (e.g., linking `CASE` to `PATIENT`) inside these classes.
* A function or command to build the tables in the database (like `Base.metadata.create_all(engine)` in SQLAlchemy).

---

## 3. Initial Data Population (Seeding)

Before testing, the database needs initial data. A script is required to fill the empty tables with starting records.

**What you actually code:**

* A Python function (e.g., `seed_database()`) that inserts a realistic batch of dummy data.
* Code to insert sample records, such as 5 Departments, 20 Staff members, 50 Rooms, and 100 Patients, ensuring the database is populated before running operations.

---

## 4. 3-5 Atomic Business Operations

An "atomic business operation" is a single, complete task performed in the system. "Atomic" means it either completely succeeds or completely fails without stopping halfway.

**What you actually code:**

* 3 to 5 distinct Python functions representing operations.
* Examples based on the ER diagram include `create_new_patient(name, details)`, `add_new_room(department_id, room_number)`, and `book_appointment(patient_id, room_id, time)`.
* At least one complex operation (like `book_appointment`) that interacts with multiple tables to demonstrate the use of **database transactions** (commit/rollback).

---

## 5. Performance Testing (Load Testing)

OLTP systems handle high volumes of fast transactions. Proof is needed that the code can handle a heavy load.

**What you actually code:**

* 1 or 2 Python functions that run one of the business operations from Step 4 thousands of times.
* For example, a function called `test_patient_insert_performance()` that loops 10,000 times, creating a new patient each time.
* Utilization of Python's `time` module to track start and end times, printing the exact number of seconds required to process the 10,000 operations.

---

## 6. Isolation Testing (Concurrency)

This proves the database is safe from race conditions. For instance, if two receptionists attempt to book "Room 101" at the exact same millisecond, the database must prevent a double-booking.

**What you actually code:**

* A Python function utilizing threading (e.g., Python's `concurrent.futures` or `threading` module) to simulate two simultaneous actions.
* An example where Thread A and Thread B simultaneously call `book_appointment()` for the exact same Room and Time.
* Execution output that proves the database locked the row, allowing one thread to succeed while the other raises an error or rolls back.