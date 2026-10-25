-- =====================================================================================
-- CloudSched  |  02_views.sql  |  Views that feed the dashboard and the Banker's procedure
-- =====================================================================================
USE cloudsched;

-- Host resource usage. Derived on the fly: allocated = SUM of ACTIVE allocations, available = total - allocated.
CREATE OR REPLACE VIEW v_resource_usage AS
SELECT r.id AS resource_id, r.resource_type, r.unit, r.total_capacity,
       COALESCE(SUM(a.amount), 0)                                   AS allocated,
       r.total_capacity - COALESCE(SUM(a.amount), 0)                AS available,
       ROUND(100 * COALESCE(SUM(a.amount), 0) / r.total_capacity, 1) AS utilization_pct
FROM resources r
LEFT JOIN allocations a ON a.resource_id = r.id AND a.released_at IS NULL
GROUP BY r.id, r.resource_type, r.unit, r.total_capacity;

-- Jobs per status (dashboard counters)
CREATE OR REPLACE VIEW v_job_status_summary AS
SELECT status, COUNT(*) AS job_count, SUM(cpu_cores) AS total_cpu_requested, SUM(ram_mb) AS total_ram_requested
FROM jobs GROUP BY status;

-- Ready queue: jobs waiting for resources, with how long they have waited (real seconds) for priority aging.
CREATE OR REPLACE VIEW v_waiting_queue AS
SELECT j.id AS job_id, j.name, j.priority, j.arrival_time, j.est_time, j.cpu_cores, j.ram_mb, j.storage_mb,
       TIMESTAMPDIFF(SECOND, j.created_at, NOW()) AS waited_seconds
FROM jobs j
WHERE j.status IN ('SUBMITTED', 'WAITING');

-- Running jobs with owner, VM and container
CREATE OR REPLACE VIEW v_running_jobs AS
SELECT j.id AS job_id, j.name, u.username AS owner, j.cpu_cores, j.ram_mb, j.storage_mb, j.priority,
       v.name AS vm_name, c.docker_container_id, c.status AS container_status, j.updated_at AS started_at
FROM jobs j
LEFT JOIN users u      ON u.id = j.user_id
LEFT JOIN containers c ON c.job_id = j.id
LEFT JOIN vms v        ON v.id = c.vm_id
WHERE j.status = 'RUNNING';

-- Completed jobs with the time they were running, taken from the status log
CREATE OR REPLACE VIEW v_completed_jobs AS
SELECT j.id AS job_id, j.name, u.username AS owner, j.status,
       MIN(CASE WHEN h.new_status = 'RUNNING' THEN h.changed_at END)                      AS started_at,
       MAX(CASE WHEN h.new_status IN ('COMPLETED','FAILED') THEN h.changed_at END)        AS finished_at
FROM jobs j
LEFT JOIN users u ON u.id = j.user_id
JOIN execution_history h ON h.job_id = j.id
WHERE j.status IN ('COMPLETED', 'FAILED')
GROUP BY j.id, j.name, u.username, j.status;

-- Scheduling metrics per job and algorithm, from the executed Gantt segments:
--   response = first start - arrival;  turnaround = completion - arrival;  waiting = turnaround - burst
CREATE OR REPLACE VIEW v_job_metrics AS
SELECT j.id AS job_id, j.name, e.algorithm, j.arrival_time, j.est_time AS burst_time,
       MIN(e.start_time) AS first_start, MAX(e.end_time) AS completion_time,
       MIN(e.start_time) - j.arrival_time                     AS response_time,
       MAX(e.end_time) - j.arrival_time                       AS turnaround_time,
       MAX(e.end_time) - j.arrival_time - j.est_time          AS waiting_time,
       (j.deadline IS NOT NULL AND MAX(e.end_time) > j.deadline) AS missed_deadline
FROM jobs j
JOIN job_executions e ON e.job_id = j.id
GROUP BY j.id, j.name, e.algorithm, j.arrival_time, j.est_time, j.deadline;

-- Gantt chart rows (Chart.js reads this)
CREATE OR REPLACE VIEW v_gantt AS
SELECT e.id AS segment_id, e.algorithm, j.id AS job_id, j.name AS job_name, e.start_time, e.end_time,
       e.end_time - e.start_time AS duration, v.name AS vm_name
FROM job_executions e
JOIN jobs j ON j.id = e.job_id
LEFT JOIN vms v ON v.id = e.vm_id;

-- VM capacity: used = limits of active containers (CREATED or RUNNING), free = VM size - used
CREATE OR REPLACE VIEW v_vm_status AS
SELECT v.id AS vm_id, v.name, v.status, v.vcpu, v.vram_mb, v.vdisk_mb,
       COALESCE(SUM(c.cpu_limit), 0)                AS used_vcpu,
       COALESCE(SUM(c.mem_limit_mb), 0)             AS used_vram_mb,
       v.vcpu    - COALESCE(SUM(c.cpu_limit), 0)    AS free_vcpu,
       v.vram_mb - COALESCE(SUM(c.mem_limit_mb), 0) AS free_vram_mb,
       COUNT(c.id)                                  AS active_containers
FROM vms v
LEFT JOIN containers c ON c.vm_id = v.id AND c.status IN ('CREATED', 'RUNNING')
GROUP BY v.id, v.name, v.status, v.vcpu, v.vram_mb, v.vdisk_mb;

CREATE OR REPLACE VIEW v_container_status AS
SELECT c.id AS container_id, c.docker_container_id, j.name AS job_name, v.name AS vm_name,
       c.cpu_limit, c.mem_limit_mb, c.status, c.exit_code, c.started_at, c.finished_at
FROM containers c
JOIN jobs j ON j.id = c.job_id
JOIN vms v  ON v.id = c.vm_id;

-- The three Banker's matrices in one view (one row per job x resource). Max is stored, Allocation and Need are derived.
-- Only jobs still in the system (WAITING, RUNNING) take part in the safety check.
-- Available vector = v_resource_usage.available.
CREATE OR REPLACE VIEW v_banker_matrices AS
SELECT bm.job_id, j.name AS job_name, j.status, r.resource_type,
       bm.max_demand,
       COALESCE(a.amount, 0)                   AS allocation,
       bm.max_demand - COALESCE(a.amount, 0)   AS need
FROM banker_matrices bm
JOIN jobs j      ON j.id = bm.job_id
JOIN resources r ON r.id = bm.resource_id
LEFT JOIN allocations a ON a.job_id = bm.job_id AND a.resource_id = bm.resource_id AND a.released_at IS NULL
WHERE j.status IN ('WAITING', 'RUNNING');

-- Adaptive scheduler: every comparison with each algorithm ranked by score (1 = recommended)
CREATE OR REPLACE VIEW v_algorithm_comparison AS
SELECT r.comparison_id, c.created_at, c.job_count, r.algorithm, r.avg_waiting, r.avg_turnaround, r.avg_response,
       r.max_waiting, r.cpu_util_pct, r.throughput, r.score, r.is_recommended,
       RANK() OVER (PARTITION BY r.comparison_id ORDER BY r.score) AS score_rank
FROM algorithm_results r
JOIN comparison_runs c ON c.id = r.comparison_id;

-- Per-user summary
CREATE OR REPLACE VIEW v_user_job_summary AS
SELECT u.id AS user_id, u.username, COUNT(j.id) AS total_jobs,
       SUM(j.status = 'RUNNING') AS running, SUM(j.status IN ('SUBMITTED','WAITING')) AS waiting,
       SUM(j.status = 'COMPLETED') AS completed, SUM(j.status = 'FAILED') AS failed
FROM users u LEFT JOIN jobs j ON j.user_id = u.id
GROUP BY u.id, u.username;
