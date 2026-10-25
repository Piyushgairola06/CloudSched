-- =====================================================================================
-- CloudSched  |  06_analytical_queries.sql  |  Joins, aggregates, subqueries, window functions, EXPLAIN
-- Run after 05_seed_data.sql. Each query says which dashboard widget or report it serves.
-- =====================================================================================
USE cloudsched;

-- Q1  Average waiting / turnaround / response time per algorithm (aggregate over a VIEW)          [bar chart]
SELECT algorithm,
       ROUND(AVG(waiting_time), 2)    AS avg_waiting,
       ROUND(AVG(turnaround_time), 2) AS avg_turnaround,
       ROUND(AVG(response_time), 2)   AS avg_response,
       MAX(waiting_time)              AS worst_wait,
       SUM(missed_deadline)           AS deadline_misses
FROM v_job_metrics
GROUP BY algorithm
ORDER BY avg_waiting;

-- Q2  Jobs per status                                                                              [pie chart]
SELECT * FROM v_job_status_summary ORDER BY job_count DESC;

-- Q3  Host resource usage right now                                                                [gauges]
SELECT resource_type, total_capacity, allocated, available, utilization_pct, unit FROM v_resource_usage;

-- Q4  Running jobs with owner, VM and container (3-table LEFT JOIN view)                           [running table]
SELECT * FROM v_running_jobs;

-- Q5  Waiting queue in the order each policy would pick (aging: -1 priority per 60 s waited)
SELECT job_id, name, priority, est_time, arrival_time, waited_seconds,
       GREATEST(1, priority - FLOOR(waited_seconds / 60)) AS effective_priority
FROM v_waiting_queue
ORDER BY effective_priority, arrival_time, job_id;

-- Q6  Resource consumption per user (JOIN + GROUP BY + HAVING): who holds resources right now?
SELECT u.username, COUNT(DISTINCT j.id) AS running_jobs,
       SUM(CASE WHEN r.resource_type = 'CPU' THEN a.amount END) AS cpu_cores_held,
       SUM(CASE WHEN r.resource_type = 'RAM' THEN a.amount END) AS ram_mb_held
FROM allocations a
JOIN jobs j      ON j.id = a.job_id
JOIN users u     ON u.id = j.user_id
JOIN resources r ON r.id = a.resource_id
WHERE a.released_at IS NULL
GROUP BY u.id, u.username
HAVING cpu_cores_held > 0;

-- Q7  Jobs that waited longer than the average of their own algorithm (correlated subquery)
SELECT m.algorithm, m.name, m.waiting_time
FROM v_job_metrics m
WHERE m.waiting_time > (SELECT AVG(m2.waiting_time) FROM v_job_metrics m2 WHERE m2.algorithm = m.algorithm)
ORDER BY m.algorithm, m.waiting_time DESC;

-- Q8  Starvation check: for each algorithm, the most-starved job and how many times the average it waited
SELECT algorithm, name AS most_starved_job, waiting_time,
       ROUND(waiting_time / NULLIF((SELECT AVG(m2.waiting_time) FROM v_job_metrics m2 WHERE m2.algorithm = x.algorithm), 0), 2) AS times_average
FROM (
  SELECT m.*, ROW_NUMBER() OVER (PARTITION BY m.algorithm ORDER BY m.waiting_time DESC, m.job_id) AS rn
  FROM v_job_metrics m
) x
WHERE rn = 1;

-- Q9  Algorithm ranking per comparison with window function RANK (what the dashboard "recommendation" card shows)
SELECT comparison_id, algorithm, score, score_rank, is_recommended
FROM v_algorithm_comparison
ORDER BY comparison_id, score_rank, algorithm;

-- Q10 Which algorithm wins most often? (aggregate over the recommended flag)
SELECT algorithm, COUNT(*) AS times_recommended
FROM algorithm_results WHERE is_recommended = 1
GROUP BY algorithm ORDER BY times_recommended DESC;

-- Q11 Job lifecycle audit trail written by the trigger, with time spent in each state (LEAD window function)
SELECT j.name, h.old_status, h.new_status, h.changed_at,
       TIMESTAMPDIFF(MICROSECOND, h.changed_at,
         LEAD(h.changed_at) OVER (PARTITION BY h.job_id ORDER BY h.changed_at, h.id)) / 1000 AS ms_until_next_change
FROM execution_history h
JOIN jobs j ON j.id = h.job_id
ORDER BY j.name, h.changed_at, h.id;

-- Q12 VM utilisation (LEFT JOIN view): which VMs are full, which have room?                        [VM table]
SELECT name, status, vcpu, used_vcpu, free_vcpu, vram_mb, used_vram_mb, free_vram_mb, active_containers
FROM v_vm_status ORDER BY vm_id;

-- Q13 Jobs that have NO container (NOT EXISTS anti-join): running jobs not yet placed on a VM
SELECT j.id, j.name
FROM jobs j
WHERE j.status = 'RUNNING'
  AND NOT EXISTS (SELECT 1 FROM containers c WHERE c.job_id = j.id);

-- Q14 Gantt chart data for one algorithm                                                           [Gantt chart]
SELECT job_name, start_time, end_time, duration FROM v_gantt WHERE algorithm = 'PRIORITY' ORDER BY start_time;

-- Q15 Banker's matrices as the textbook shows them: Max | Allocation | Need, plus the Available vector
SELECT job_name, resource_type, max_demand, allocation, need FROM v_banker_matrices ORDER BY job_name, resource_type;
SELECT resource_type, available FROM v_resource_usage;

-- Q16 Integrity self-check: must return 0 rows. Allocated can never exceed capacity.
SELECT * FROM v_resource_usage WHERE allocated > total_capacity OR available < 0;

-- Q17 Integrity self-check: must return 0 rows. A job that is not RUNNING must hold no active allocation.
SELECT j.id, j.name, j.status
FROM jobs j JOIN allocations a ON a.job_id = j.id
WHERE a.released_at IS NULL AND j.status <> 'RUNNING';

-- Q18 EXPLAIN: the ready-queue lookup uses ix_jobs_queue (status, priority, arrival_time) - no filesort
EXPLAIN SELECT id, name FROM jobs WHERE status = 'WAITING' ORDER BY priority, arrival_time;
-- Compare with a forced full scan to show what the index saves:
EXPLAIN SELECT id, name FROM jobs IGNORE INDEX (ix_jobs_queue, ix_jobs_status) WHERE status = 'WAITING' ORDER BY priority, arrival_time;
