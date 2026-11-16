# DBMS demo, tests, backup/restore and viva notes

## 1. Install (needs MySQL 8.0.16+)

```
cd database
mysql -u cloudsched_user -p < install.sql      # drops + recreates tables, loads views, procedures, triggers, demo data
```
`mysql --version` must say 8.0.16 or higher. Older servers accept `CHECK (...)` but silently ignore it.
In Workbench: open and run `01_schema.sql`, `02_views.sql`, `03_procedures.sql`, `04_triggers.sql`, `05_seed_data.sql` in that order.

**Expected state after the seed** (use it as a checklist):

| Check | Command | Expected |
|---|---|---|
| 11 tables | `SHOW TABLES;` | 11 tables + 12 views |
| Job states | `SELECT * FROM v_job_status_summary;` | COMPLETED 4 (P1..P4), RUNNING 2 (ml-train, etl-batch), WAITING 2 (render-big, log-clean) |
| Host usage | `SELECT * FROM v_resource_usage;` | CPU 6 of 8 allocated, RAM 12288 of 16384 |
| Containers | `SELECT * FROM v_vm_status;` | vm-large 4/4 vCPU used, vm-medium 2/2 used, vm-small STOPPED |
| Recommendation | `SELECT algorithm FROM algorithm_results WHERE is_recommended;` | PRIORITY (SJF has the same score; ties go alphabetically) |
| History | `SELECT * FROM execution_history ORDER BY id;` | SUBMITTED → WAITING → RUNNING → COMPLETED rows written by the trigger, not by Python |

If any file stops with an error, copy the exact error message and the line it names: nearly all problems are server version, a missing privilege, or a file run out of order.

## 2. Seven-minute demo script

1. **Schema (1 min).** Show the ER diagram in `docs/DBMS_DESIGN.md`, point at the 3NF argument (no stored *available*, no stored *need*).
2. **Constraints (1 min).** `07_transactions_demo.sql` demo 2: every statement fails with a different reason (CHECK, FK, state-machine trigger, protect-running trigger, VM capacity trigger).
3. **Trigger automation (1 min).** Demo 7: `CALL sp_release_job(...)` → run `SELECT * FROM v_resource_usage` (cores freed by the trigger) and `SELECT * FROM execution_history`.
4. **Banker's in SQL (1 min).** `SELECT * FROM v_banker_matrices;` (Max | Allocation | Need) then `CALL sp_allocate_job(<render-big id>, @r); SELECT @r;` → `DEFERRED_UNAVAILABLE`.
5. **Concurrency (2 min).** Demo 3 in two tabs, or `pytest tests/test_mysql_concurrency.py -v` (4 threads race for the last cores; exactly one `GRANTED`). Then the manual lock demo with `performance_schema.data_lock_waits`.
6. **Analytics (1 min).** Q1 (average waiting per algorithm), Q9 (ranking with RANK), Q18 (EXPLAIN with and without the queue index).

## 3. How the project's DBMS test rows are covered

| Report row | How to run it | Pass condition |
|---|---|---|
| Schema and constraint test | demo 2 statements | every statement is rejected |
| Query test | `06_analytical_queries.sql` | Q1 matches `docs/OS_DEMO.md` (FCFS 5.75, SJF/PRIORITY 5.25, RR 9.75 avg waiting) |
| Trigger test | demo 7, then `SELECT * FROM allocations WHERE released_at IS NOT NULL` | resources released and history logged automatically |
| Transaction and concurrency test | `MYSQL_TEST_URL=... pytest tests/test_mysql_concurrency.py -v` and demo 3 | one `GRANTED`, rest `DEFERRED_UNAVAILABLE`, `allocated ≤ total`; demo 1 shows rollback leaves no rows |
| Integrity self-checks | Q16 and Q17 | both return 0 rows |

## 4. Backup and restore

Run in a normal terminal (not inside `mysql`). On Windows add `C:\Program Files\MySQL\MySQL Server 8.0\bin` to `PATH` or use the full path.

```
:: BACKUP: tables + data + views + triggers + stored procedures, consistent snapshot, no table locks
mysqldump -u cloudsched_user -p --single-transaction --routines --triggers --events --no-tablespaces cloudsched > cloudsched_backup.sql

:: RESTORE into a fresh database (use root, the limited user cannot create databases)
mysql -u root -p -e "DROP DATABASE IF EXISTS cloudsched_restore; CREATE DATABASE cloudsched_restore CHARACTER SET utf8mb4;"
mysql -u root -p cloudsched_restore < cloudsched_backup.sql

:: VERIFY the restore
mysql -u root -p -e "CHECKSUM TABLE cloudsched.jobs, cloudsched_restore.jobs; SELECT COUNT(*) FROM cloudsched_restore.jobs;"
mysql -u root -p -e "SHOW TRIGGERS FROM cloudsched_restore; SHOW PROCEDURE STATUS WHERE Db='cloudsched_restore';"
```
Linux/macOS: identical commands, the `::` lines are comments. To restore over the live database instead, drop it first and restore into `cloudsched`.
If `--routines` reports a privilege error, run the backup as root. Point-in-time recovery is possible by also enabling the binary log (`log_bin`) and replaying it with `mysqlbinlog` after restoring the last full dump; this project only needs the full-dump workflow.

Suggested least-privilege split (optional, shows DBMS security): the application account gets `SELECT, INSERT, UPDATE, DELETE, EXECUTE` on `cloudsched.*`, and only the administrator keeps `CREATE/DROP/ALTER/TRIGGER`.

## 5. Viva cheat-sheet

* **Why is `available` not a column?** It is derived (`total - SUM(active allocations)`). A stored copy would have to be updated in every allocation and release, and one missed update makes it wrong. The view cannot drift.
* **How do you stop two jobs taking the same CPU?** `sp_allocate_job` locks the resource rows with `SELECT … FOR UPDATE` in a fixed order. The second transaction waits, then sees the reduced availability.
* **Why a fixed lock order?** Two transactions that lock the same rows in opposite orders deadlock (demo 5). A fixed order makes a cycle impossible.
* **Why take the locks before reading availability?** Under `REPEATABLE READ` a plain read creates a snapshot. If the snapshot were created before the lock was granted it could be stale. Locking first guarantees fresh data.
* **Which isolation level?** InnoDB's default `REPEATABLE READ`. The procedure is correct under `READ COMMITTED` too (demo 4 shows the difference).
* **Trigger vs procedure?** A trigger fires on every write by anyone, so it holds the rules that must never be bypassed (state machine, release on completion, audit log). A procedure is called on purpose and holds the multi-step workflow (allocate).
* **Where is Banker's algorithm?** In Python (`bankers_algorithm.py`) for the demo and in SQL (`sp_bankers_check`) for the database-backed flow. Both use the same safety algorithm; Max is stored, Allocation and Need come from views.
* **Why is `algorithm_results` allowed to store computed numbers?** They are a snapshot of one simulation, not a copy of live data.
* **What is not solved?** Single-host locking is a bottleneck; Banker's with Max = request is nearly always safe; aging in SQL uses real seconds (see "Known limits" in `DBMS_DESIGN.md`).
