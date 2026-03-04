Classical Online Transaction Processing (OLTP): Architectural Foundations and Market State (2026)

Executive Summary

This document provides a technical overview of classical Online Transaction Processing (OLTP) systems, based on insights from the 2026 lecture by Nikolay Golov. The analysis covers the competitive landscape of Database Management Systems (DBMS), the fundamental client-server architecture, and the algorithmic implementations of transaction integrity (ACID). Key findings highlight the continued dominance of Oracle and PostgreSQL in the market, the shift from rollback journals to Write-Ahead Logs (WAL) for performance, and the critical role of isolation levels in managing concurrent data access.

The Data Storage Landscape

Database systems are categorized by their primary use cases. While OLTP focuses on high-concurrency, short-lived transactions, other systems are optimized for different workloads:

* OLTP (Online Transaction Processing): Designed for transactional integrity and rapid read/write operations.
* Data Warehouses and Analytics: Optimized for complex queries and large-scale data processing.
* Specialized Applications: Systems tailored for graph databases, matrix operations, and web services.

Market Rankings (February 2026)

The DBMS market remains concentrated among a few key players, characterized by relational and multi-model capabilities.

Rank (Feb 2026)	DBMS	Database Model	Score
1	Oracle	Relational, Multi-model	1203.51
2	MySQL	Relational, Multi-model	868.22
3	Microsoft SQL Server	Relational, Multi-model	708.14
4	PostgreSQL	Relational, Multi-model	672.03

According to Stack Overflow's 2026 data, developer preference shows a strong lean toward open-source solutions:

* PostgreSQL: 55.6%
* MySQL: 40.5%
* SQLite: 37.5%
* Microsoft SQL Server: 30.1%

Client-Server Architecture

Classical OLTP systems operate on a layered architecture that separates the application logic from the physical storage:

1. Application: The end-user software executing business logic.
2. DB Driver: The interface layer enabling communication between the application and the server.
3. Database Server: The engine that processes queries, manages transactions, and ensures data integrity.
4. Files with Data: The physical storage on disk where the information persists.

Transaction Integrity: ACID Properties

The lecture emphasizes two specific pillars of ACID (Atomicity, Consistency, Isolation, Durability) within classical OLTP:

Durability through Master/Slave Configurations

To ensure data is not lost during a system failure, OLTP systems employ Master/Slave (Standby) architectures:

* Master: Handles the primary write operations.
* Slave: Maintains a copy of the data.
* Successful Write: Data is confirmed only after being securely handled by the master and/or distributed to slaves to prevent data loss upon a master failure.

Isolation and Concurrency Control

Isolation prevents transactions from interfering with one another. A primary challenge is the "dirty read," where a transaction reads uncommitted data from another transaction.

Isolation Levels

* Read Committed: The database ensures that a transaction only sees data that was finalized (committed) before a specific read operation.
* Repeatable Read: A stricter level where "each transaction sees only transactions, finalized before its start." This prevents the data from changing even if another transaction commits changes during the current transaction's lifespan.

Transactional Algorithms

The method by which a database handles changes determines its performance and reliability.

1. Rollback Journal

This is a traditional approach to managing updates:

* Process: Before modifying a file, the system creates a "rollback copy."
* Commit: If the operation is successful, the copy is dropped.
* Fail: If the system fails, the rollback copy is used to restore the original state of the data file.

2. Write-Ahead-Log (WAL)

WAL is the modern standard for transaction logging, utilized by engines like PostgreSQL:

* Initiation: A transaction starts within the WAL.
* Logging: All changes are written to the WAL log file first, rather than the main data file.
* Commit/Rollback: Upon success, the change is committed to the WAL. If it fails, a rollback is recorded in the WAL.
* Checkpointing: The system periodically merges the WAL log with the primary database data files.

Concurrency Implementation Logic

To handle multiple transactions updating the same record (e.g., UPDATE T SET V=V+1), databases use locking mechanisms:

1. Transaction 1 selects a value and locks it.
2. Transaction 2 attempts to select the same value but must "wait" for the lock to be released.
3. Transaction 1 updates and unlocks.
4. Transaction 2 then proceeds with the updated value, preventing "lost updates."
