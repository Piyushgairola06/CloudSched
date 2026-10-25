-- =====================================================================================
-- CloudSched  |  07_transactions_demo.sql  |  Transactions, rollback, isolation levels, locking, deadlock
-- This is a SCRIPT TO READ AND RUN BY HAND in two MySQL Workbench query tabs / two terminals (Session A, Session B).
-- Run the numbered steps in the order shown. It is not meant to be executed top-to-bottom as one file.
-- The demos change the data, so exact numbers shift between demos; re-run 01..05 for a clean state.
-- =====================================================================================
USE cloudsched;

-- -------------------------------------------------------------------------------------
-- DEMO 1  ATOMICITY: a failed allocation rolls back completely (nothing half-allocated)
-- -------------------------------------------------------------------------------------
SELECT resource_type, allocated, available FROM v_resource_usage;          -- note the numbers
CALL sp_submit_job(2, 'demo-atomic', 1, 256, 10, 5, 5, 0, NULL, 'CPU_BOUND', @j);
START TRANSACTION;
  INSERT INTO allocations (job_id, resource_id, amount) VALUES (@j, 1, 1);  -- succeeds (inside the open transaction)
  -- The next insert asks for more RAM than the job's declared Max (Banker rule, trigger T5) -> ERROR.
  INSERT INTO allocations (job_id, resource_id, amount) VALUES (@j, 2, 999999);
  -- Workbench stops the script at the error; run the next line by hand:
ROLLBACK;                                                                   -- the first insert is undone too
SELECT resource_type, allocated, available FROM v_resource_usage;          -- identical to before
SELECT COUNT(*) AS active_rows FROM allocations WHERE job_id = @j;         -- 0

-- SAVEPOINT: undo only the last step
START TRANSACTION;
  UPDATE jobs SET priority = 1 WHERE id = @j;
  SAVEPOINT after_priority;
  UPDATE jobs SET est_time = 99 WHERE id = @j;
  ROLLBACK TO SAVEPOINT after_priority;                                     -- est_time change undone, priority kept
COMMIT;
SELECT priority, est_time FROM jobs WHERE id = @j;                          -- 1, 5

-- -------------------------------------------------------------------------------------
-- DEMO 2  CONSISTENCY: constraints and triggers reject invalid data (each statement below must FAIL)
-- -------------------------------------------------------------------------------------
INSERT INTO jobs (name, cpu_cores, ram_mb, est_time) VALUES ('bad', 0, 100, 5);           -- CHECK cpu_cores > 0
INSERT INTO jobs (name, cpu_cores, ram_mb, est_time, priority) VALUES ('bad', 1, 100, 5, 11); -- CHECK priority 1..10
INSERT INTO allocations (job_id, resource_id, amount) VALUES (99999, 1, 1);               -- FOREIGN KEY: no such job
UPDATE jobs SET status = 'COMPLETED' WHERE name = 'render-big';                           -- trigger T1: WAITING -> COMPLETED not allowed
DELETE FROM jobs WHERE name = 'ml-train';                                                 -- trigger T4: job is RUNNING
INSERT INTO vms (name, vcpu, vram_mb, vdisk_mb) VALUES ('too-big', 4, 1024, 1024);        -- trigger T6: VMs would exceed host CPU

-- -------------------------------------------------------------------------------------
-- DEMO 3  ISOLATION + CONCURRENCY: two jobs race for the last cores; only one may win
-- Setup: make two jobs that each need ALL currently free CPU (free cores = SELECT available ... 'CPU').
-- -------------------------------------------------------------------------------------
SELECT available INTO @free_cpu FROM v_resource_usage WHERE resource_type = 'CPU';
CALL sp_submit_job(2, 'race-1', @free_cpu, 128, 0, 5, 5, 0, NULL, 'CPU_BOUND', @a);
CALL sp_submit_job(3, 'race-2', @free_cpu, 128, 0, 5, 5, 0, NULL, 'CPU_BOUND', @b);
SELECT @a AS job_a, @b AS job_b;                                             -- write these two ids down

-- Session A:  CALL sp_allocate_job(<job_a>, @r);  SELECT @r;
-- Session B:  CALL sp_allocate_job(<job_b>, @r);  SELECT @r;   (start it within the same second as A)
-- Expected: one session prints GRANTED, the other DEFERRED_UNAVAILABLE. Never two GRANTED.
-- Why: both first lock the CPU row in `resources` (SELECT ... FOR UPDATE). The second waits there until the first
-- COMMITs, then reads the already reduced "available" and is refused.
-- Proof afterwards (must show allocated <= total):
SELECT * FROM v_resource_usage;

-- Manual version, to SEE the lock (two tabs):
--   Session A:  START TRANSACTION;  SELECT * FROM resources WHERE resource_type='CPU' FOR UPDATE;   -- holds the lock
--   Session B:  START TRANSACTION;  SELECT * FROM resources WHERE resource_type='CPU' FOR UPDATE;   -- BLOCKS (waiting)
--   Session C:  SELECT * FROM performance_schema.data_lock_waits;     -- shows B waiting for A
--   Session A:  COMMIT;     -> Session B continues.

-- -------------------------------------------------------------------------------------
-- DEMO 4  ISOLATION LEVELS on the same data (utilisation read twice inside one transaction)
-- -------------------------------------------------------------------------------------
-- (Needs a running job named 'etl-batch'. Let X = the CPU 'allocated' value A sees first.)
-- Session A:  SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED;
--             START TRANSACTION;  SELECT allocated FROM v_resource_usage WHERE resource_type='CPU';   -- X
-- Session B:  CALL sp_release_job((SELECT id FROM jobs WHERE name='etl-batch'), 'COMPLETED');          -- frees 2 cores, commits
-- Session A:  SELECT allocated FROM v_resource_usage WHERE resource_type='CPU';   -- READ COMMITTED -> X-2 (non-repeatable read)
--             COMMIT;
-- Repeat with  SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ;  (MySQL/InnoDB default):
--             A's second SELECT still shows X: it reads its own snapshot. Same query, different guarantee.
-- Check the current level:  SELECT @@transaction_isolation;
-- Our procedure takes its locks FIRST, so it reads fresh data under either level.

-- -------------------------------------------------------------------------------------
-- DEMO 5  DEADLOCK: why sp_allocate_job locks CPU -> RAM -> STORAGE in a fixed order
-- -------------------------------------------------------------------------------------
-- Session A:  START TRANSACTION;  SELECT * FROM resources WHERE resource_type='CPU' FOR UPDATE;
-- Session B:  START TRANSACTION;  SELECT * FROM resources WHERE resource_type='RAM' FOR UPDATE;
-- Session A:  SELECT * FROM resources WHERE resource_type='RAM' FOR UPDATE;     -- waits for B
-- Session B:  SELECT * FROM resources WHERE resource_type='CPU' FOR UPDATE;     -- cycle! InnoDB aborts one with
--             ERROR 1213 (40001): Deadlock found when trying to get lock; try restarting transaction
-- Inspect:    SHOW ENGINE INNODB STATUS\G      (section "LATEST DETECTED DEADLOCK")
-- Fix used by the project: every transaction takes the locks in the SAME order, so a cycle cannot form.
-- If a deadlock still happens (for example from user code), the application must catch error 1213 and RETRY.

-- -------------------------------------------------------------------------------------
-- DEMO 6  SKIP LOCKED: several scheduler workers pull different jobs from the queue without blocking each other
-- -------------------------------------------------------------------------------------
-- Worker 1:  START TRANSACTION;
--            SELECT id, name FROM jobs WHERE status='WAITING' ORDER BY priority, arrival_time LIMIT 1 FOR UPDATE SKIP LOCKED;
-- Worker 2 (while worker 1 has not committed):
--            START TRANSACTION;
--            SELECT id, name FROM jobs WHERE status='WAITING' ORDER BY priority, arrival_time LIMIT 1 FOR UPDATE SKIP LOCKED;
--            -- returns the NEXT job instead of waiting for worker 1
-- Both:      ROLLBACK;

-- -------------------------------------------------------------------------------------
-- DEMO 7  Automatic release + re-dispatch (trigger T3 + sp_dispatch_waiting)
-- -------------------------------------------------------------------------------------
SELECT resource_type, allocated, available FROM v_resource_usage;
CALL sp_release_job((SELECT id FROM jobs WHERE name = 'ml-train'), 'COMPLETED');   -- trigger frees 4 cores, closes container
SELECT resource_type, allocated, available FROM v_resource_usage;                  -- CPU allocated dropped by 4
CALL sp_dispatch_waiting('PRIORITY_AGING', 60, @started);                           -- waiting jobs start in policy order
SELECT @started AS jobs_started;
SELECT * FROM v_running_jobs;
