-- =====================================================================================
-- CloudSched  |  05_seed_data.sql  |  Demo data. Run ONCE, right after 01..04 (it is not idempotent).
-- It uses the stored procedures on purpose, so the triggers fill execution_history / banker_matrices / allocations.
-- The textbook workload P1..P4 and the algorithm numbers are the real output of backend/app/services
-- (adaptive_scheduler.evaluate with quantum 2), the same example used in docs/OS_DEMO.md.
-- =====================================================================================
USE cloudsched;

-- ---------------------------------------------------------------- users (password_hash is a dummy value, not a real hash)
INSERT INTO users (username, email, password_hash, role) VALUES
  ('admin', 'admin@cloudsched.local', 'seed-not-a-real-hash', 'ADMIN'),
  ('alice', 'alice@cloudsched.local', 'seed-not-a-real-hash', 'USER'),
  ('bob',   'bob@cloudsched.local',   'seed-not-a-real-hash', 'USER'),
  ('carol', 'carol@cloudsched.local', 'seed-not-a-real-hash', 'USER');

-- ---------------------------------------------------------------- physical host (same numbers as backend/.env.example)
INSERT INTO resources (resource_type, total_capacity, unit) VALUES
  ('CPU', 8, 'cores'), ('RAM', 16384, 'MB'), ('STORAGE', 102400, 'MB');

-- ---------------------------------------------------------------- hypervisor: 3 VMs = 8 vCPU, 16384 MB (fits the host)
INSERT INTO vms (name, vcpu, vram_mb, vdisk_mb) VALUES
  ('vm-large',  4, 8192, 40960),
  ('vm-medium', 2, 4096, 20480),
  ('vm-small',  2, 4096, 20480);
UPDATE vms SET status = 'RUNNING';                       -- CREATED -> RUNNING (lifecycle trigger checks it)
UPDATE vms SET status = 'STOPPED' WHERE name = 'vm-small';

-- ---------------------------------------------------------------- textbook workload P1..P4 (owner: alice)
--                    user, name, cpu, ram, storage, est, prio, arrival, deadline, type, OUT id
CALL sp_submit_job(2, 'P1', 2, 2048, 500, 5, 2, 0, NULL, 'CPU_BOUND', @j);
CALL sp_submit_job(2, 'P2', 2, 2048, 500, 3, 1, 1, NULL, 'CPU_BOUND', @j);
CALL sp_submit_job(2, 'P3', 2, 2048, 500, 8, 4, 2, NULL, 'CPU_BOUND', @j);
CALL sp_submit_job(2, 'P4', 2, 2048, 500, 6, 3, 3, NULL, 'CPU_BOUND', @j);

-- Run them one after another in the order the PRIORITY schedule chose: P1, P2, P4, P3 (full lifecycle through the procedures)
CALL sp_allocate_job((SELECT id FROM jobs WHERE name = 'P1'), @r);  CALL sp_release_job((SELECT id FROM jobs WHERE name = 'P1'), 'COMPLETED');
CALL sp_allocate_job((SELECT id FROM jobs WHERE name = 'P2'), @r);  CALL sp_release_job((SELECT id FROM jobs WHERE name = 'P2'), 'COMPLETED');
CALL sp_allocate_job((SELECT id FROM jobs WHERE name = 'P4'), @r);  CALL sp_release_job((SELECT id FROM jobs WHERE name = 'P4'), 'COMPLETED');
CALL sp_allocate_job((SELECT id FROM jobs WHERE name = 'P3'), @r);  CALL sp_release_job((SELECT id FROM jobs WHERE name = 'P3'), 'COMPLETED');

-- ---------------------------------------------------------------- Gantt segments of the 4 schedules (from scheduling_algorithms.py)
INSERT INTO job_executions (job_id, algorithm, start_time, end_time)
SELECT j.id, s.algo, s.st, s.en
FROM jobs j
JOIN (
  SELECT 'P1' AS pname, 'FCFS' AS algo, 0 AS st, 5 AS en UNION ALL SELECT 'P2','FCFS',5,8   UNION ALL
  SELECT 'P3','FCFS',8,16  UNION ALL SELECT 'P4','FCFS',16,22 UNION ALL
  SELECT 'P1','SJF',0,5    UNION ALL SELECT 'P2','SJF',5,8    UNION ALL
  SELECT 'P4','SJF',8,14   UNION ALL SELECT 'P3','SJF',14,22  UNION ALL
  SELECT 'P1','PRIORITY',0,5  UNION ALL SELECT 'P2','PRIORITY',5,8   UNION ALL
  SELECT 'P4','PRIORITY',8,14 UNION ALL SELECT 'P3','PRIORITY',14,22 UNION ALL
  SELECT 'P1','RR',0,2   UNION ALL SELECT 'P2','RR',2,4   UNION ALL SELECT 'P3','RR',4,6   UNION ALL
  SELECT 'P1','RR',6,8   UNION ALL SELECT 'P4','RR',8,10  UNION ALL SELECT 'P2','RR',10,11 UNION ALL
  SELECT 'P3','RR',11,13 UNION ALL SELECT 'P1','RR',13,14 UNION ALL SELECT 'P4','RR',14,16 UNION ALL
  SELECT 'P3','RR',16,18 UNION ALL SELECT 'P4','RR',18,20 UNION ALL SELECT 'P3','RR',20,22
) s ON s.pname = j.name;

-- ---------------------------------------------------------------- adaptive scheduler: one comparison, 4 algorithms
INSERT INTO comparison_runs (requested_by, time_quantum, job_count) VALUES (2, 2, 4);
SET @cmp = LAST_INSERT_ID();
INSERT INTO algorithm_results
  (comparison_id, algorithm, avg_waiting, avg_turnaround, avg_response, max_waiting, cpu_util_pct, throughput, score) VALUES
  (@cmp, 'FCFS',     5.75, 11.25, 5.75, 13, 100, 0.1818, 0.5030),
  (@cmp, 'SJF',      5.25, 10.75, 5.25, 12, 100, 0.1818, 0.1926),
  (@cmp, 'PRIORITY', 5.25, 10.75, 5.25, 12, 100, 0.1818, 0.1926),
  (@cmp, 'RR',       9.75, 15.25, 2.00, 12, 100, 0.1818, 0.5000);
CALL sp_mark_recommended(@cmp);                          -- lowest score; tie SJF/PRIORITY -> alphabetical -> PRIORITY

-- ---------------------------------------------------------------- live workload for the dashboard
CALL sp_submit_job(2, 'ml-train',   4,  8192, 2000, 30, 2, 0, NULL, 'MIXED',        @j);
CALL sp_submit_job(3, 'etl-batch',  2,  4096, 1000, 20, 4, 1, NULL, 'IO_BOUND',     @j);
CALL sp_submit_job(3, 'render-big', 6, 12288, 4000, 40, 3, 2, NULL, 'CPU_BOUND',    @j);
CALL sp_submit_job(4, 'log-clean',  1,   512,  100,  5, 8, 3, NULL, 'IO_BOUND',     @j);

CALL sp_allocate_job((SELECT id FROM jobs WHERE name = 'ml-train'),  @r);   -- GRANTED  (4 of 8 cores)
CALL sp_allocate_job((SELECT id FROM jobs WHERE name = 'etl-batch'), @r);   -- GRANTED  (6 of 8 cores in use)
-- render-big (6 cores) and log-clean stay WAITING: run  CALL sp_allocate_job(<id>, @r); SELECT @r;  to see DEFERRED_UNAVAILABLE

CALL sp_place_job_on_vm((SELECT id FROM jobs WHERE name = 'ml-train'),  @vm);   -- best fit: vm-large
CALL sp_place_job_on_vm((SELECT id FROM jobs WHERE name = 'etl-batch'), @vm);   -- best fit: vm-medium
