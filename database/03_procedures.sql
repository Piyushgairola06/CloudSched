-- =====================================================================================
-- CloudSched  |  03_procedures.sql  |  Stored procedures (transactions, locking, Banker's, dispatch)
-- Needs the mysql command-line client or Workbench (they understand DELIMITER). PyMySQL cannot run this file.
-- Result codes deliberately match app/services/bankers_algorithm.py:
--   GRANTED | DEFERRED_UNAVAILABLE | DEFERRED_UNSAFE | INVALID
-- NOTE: sp_allocate_job / sp_release_job / sp_dispatch_waiting open their own transaction (START TRANSACTION
--       implicitly commits anything already open on that connection). Call them outside a transaction.
-- =====================================================================================
USE cloudsched;
DELIMITER $$

-- ---------------------------------------------------------------------------------------------
-- sp_submit_job: admission control + insert, one atomic transaction.
-- The AFTER INSERT trigger (04_triggers.sql) logs the status and fills banker_matrices (max = request).
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_submit_job$$
CREATE PROCEDURE sp_submit_job(
  IN  p_user_id INT, IN p_name VARCHAR(100), IN p_cpu INT, IN p_ram INT, IN p_storage INT,
  IN  p_est_time INT, IN p_priority INT, IN p_arrival INT, IN p_deadline INT, IN p_job_type VARCHAR(20),
  OUT p_job_id INT)
BEGIN
  DECLARE v_cpu_cap, v_ram_cap, v_sto_cap INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  SELECT total_capacity INTO v_cpu_cap FROM resources WHERE resource_type = 'CPU';
  SELECT total_capacity INTO v_ram_cap FROM resources WHERE resource_type = 'RAM';
  SELECT total_capacity INTO v_sto_cap FROM resources WHERE resource_type = 'STORAGE';

  -- A request that can never fit is rejected instead of waiting forever (same rule as job_service.create_job)
  IF p_cpu > v_cpu_cap OR p_ram > v_ram_cap OR p_storage > v_sto_cap THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Job request exceeds total host capacity';
  END IF;

  START TRANSACTION;
  INSERT INTO jobs (user_id, name, cpu_cores, ram_mb, storage_mb, est_time, priority, arrival_time, deadline, job_type)
  VALUES (p_user_id, p_name, p_cpu, p_ram, p_storage, p_est_time, p_priority, p_arrival, p_deadline, p_job_type);
  SET p_job_id = LAST_INSERT_ID();
  UPDATE jobs SET status = 'WAITING' WHERE id = p_job_id;       -- SUBMITTED -> WAITING (trigger logs both)
  COMMIT;
END$$

-- ---------------------------------------------------------------------------------------------
-- sp_bankers_check: Banker's safety algorithm in SQL. Does NOT change any data and opens NO transaction.
-- It asks: "if I granted this job its request right now, would a safe completion order still exist?"
-- State = every WAITING/RUNNING job (Max, Allocation, Need from v_banker_matrices) + Available from allocations.
-- p_safe: 1 = safe, 0 = unsafe.   Same algorithm as bankers_algorithm.is_safe() / request_resources().
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_bankers_check$$
CREATE PROCEDURE sp_bankers_check(IN p_job_id INT, OUT p_safe TINYINT)
BEGIN
  DECLARE w_cpu, w_ram, w_sto INT;               -- Work vector (starts as Available after the pretend grant)
  DECLARE r_cpu, r_ram, r_sto INT;               -- the candidate's request
  DECLARE t_cpu, t_ram, t_sto INT;               -- total capacity
  DECLARE u_cpu, u_ram, u_sto INT;               -- currently allocated
  DECLARE g_cpu, g_ram, g_sto INT;               -- resources returned by the job that "finishes"
  DECLARE v_pick INT;
  DECLARE v_left INT;
  DECLARE v_progress TINYINT DEFAULT 1;

  SELECT cpu_cores, ram_mb, storage_mb INTO r_cpu, r_ram, r_sto FROM jobs WHERE id = p_job_id;

  SELECT total_capacity, allocated INTO t_cpu, u_cpu FROM v_resource_usage WHERE resource_type = 'CPU';
  SELECT total_capacity, allocated INTO t_ram, u_ram FROM v_resource_usage WHERE resource_type = 'RAM';
  SELECT total_capacity, allocated INTO t_sto, u_sto FROM v_resource_usage WHERE resource_type = 'STORAGE';

  -- Pretend to grant: Available shrinks by the request
  SET w_cpu = t_cpu - u_cpu - r_cpu, w_ram = t_ram - u_ram - r_ram, w_sto = t_sto - u_sto - r_sto;

  DROP TEMPORARY TABLE IF EXISTS tmp_bk;
  CREATE TEMPORARY TABLE tmp_bk (
    job_id INT PRIMARY KEY,
    a_cpu INT, a_ram INT, a_sto INT,             -- Allocation
    n_cpu INT, n_ram INT, n_sto INT,             -- Need
    done TINYINT NOT NULL DEFAULT 0
  );
  INSERT INTO tmp_bk (job_id, a_cpu, a_ram, a_sto, n_cpu, n_ram, n_sto)
  SELECT m.job_id,
         SUM(CASE WHEN m.resource_type = 'CPU'     THEN m.allocation ELSE 0 END),
         SUM(CASE WHEN m.resource_type = 'RAM'     THEN m.allocation ELSE 0 END),
         SUM(CASE WHEN m.resource_type = 'STORAGE' THEN m.allocation ELSE 0 END),
         SUM(CASE WHEN m.resource_type = 'CPU'     THEN m.need ELSE 0 END),
         SUM(CASE WHEN m.resource_type = 'RAM'     THEN m.need ELSE 0 END),
         SUM(CASE WHEN m.resource_type = 'STORAGE' THEN m.need ELSE 0 END)
  FROM v_banker_matrices m
  GROUP BY m.job_id;

  -- Apply the pretend grant to the candidate: Allocation += request, Need -= request
  UPDATE tmp_bk
  SET a_cpu = a_cpu + r_cpu, a_ram = a_ram + r_ram, a_sto = a_sto + r_sto,
      n_cpu = n_cpu - r_cpu, n_ram = n_ram - r_ram, n_sto = n_sto - r_sto
  WHERE job_id = p_job_id;

  -- A request larger than the job's declared Max would make Need negative -> never safe/valid
  SELECT COUNT(*) INTO v_left FROM tmp_bk WHERE n_cpu < 0 OR n_ram < 0 OR n_sto < 0;
  IF v_left > 0 OR w_cpu < 0 OR w_ram < 0 OR w_sto < 0 THEN
    SET v_progress = 0;
  END IF;

  -- Safety algorithm: repeatedly find a job whose Need fits in Work, let it finish, take its Allocation back
  WHILE v_progress = 1 DO
    SET v_pick = NULL;
    SELECT job_id INTO v_pick FROM tmp_bk
    WHERE done = 0 AND n_cpu <= w_cpu AND n_ram <= w_ram AND n_sto <= w_sto
    ORDER BY job_id LIMIT 1;

    IF v_pick IS NULL THEN
      SET v_progress = 0;
    ELSE
      SELECT a_cpu, a_ram, a_sto INTO g_cpu, g_ram, g_sto FROM tmp_bk WHERE job_id = v_pick;
      SET w_cpu = w_cpu + g_cpu, w_ram = w_ram + g_ram, w_sto = w_sto + g_sto;
      UPDATE tmp_bk SET done = 1 WHERE job_id = v_pick;
    END IF;
  END WHILE;

  SELECT COUNT(*) INTO v_left FROM tmp_bk WHERE done = 0;
  SET p_safe = IF(v_left = 0, 1, 0);
  DROP TEMPORARY TABLE tmp_bk;
END$$

-- ---------------------------------------------------------------------------------------------
-- sp_allocate_job: THE concurrency-controlled allocation (ACID transaction, pessimistic row locking).
--   1. Lock the 3 resource rows in a FIXED order (CPU, RAM, STORAGE). Two allocators therefore queue up at the
--      first lock instead of deadlocking, and only one at a time can read "available" and claim it.
--   2. Lock the job row (so a concurrent cancel cannot race a start).
--   3. Check availability, run Banker's safety check, insert the 3 allocation rows, set job RUNNING, COMMIT.
--   Any error -> ROLLBACK, nothing is half-allocated.
-- IMPORTANT: all reads of shared state happen AFTER the locks are taken, so even under REPEATABLE READ the
--            consistent-read snapshot is created after the previous holder committed (no stale "available").
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_allocate_job$$
CREATE PROCEDURE sp_allocate_job(IN p_job_id INT, OUT p_result VARCHAR(30))
alloc_main: BEGIN
  DECLARE t_cpu, t_ram, t_sto INT;
DECLARE id_cpu, id_ram, id_sto INT;
  DECLARE u_cpu, u_ram, u_sto INT;
  DECLARE r_cpu, r_ram, r_sto INT;
  DECLARE v_status VARCHAR(20) DEFAULT NULL;
  DECLARE v_safe TINYINT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  START TRANSACTION;

  -- 1. lock resource rows (fixed order) and read capacities
SELECT id, total_capacity INTO id_cpu, t_cpu
FROM resources WHERE resource_type = 'CPU' FOR UPDATE;

SELECT id, total_capacity INTO id_ram, t_ram
FROM resources WHERE resource_type = 'RAM' FOR UPDATE;

SELECT id, total_capacity INTO id_sto, t_sto
FROM resources WHERE resource_type = 'STORAGE' FOR UPDATE;

  -- 2. lock the job row
  SELECT status, cpu_cores, ram_mb, storage_mb INTO v_status, r_cpu, r_ram, r_sto
  FROM jobs WHERE id = p_job_id FOR UPDATE;

  IF v_status IS NULL THEN
    ROLLBACK; SET p_result = 'INVALID'; LEAVE alloc_main;
  END IF;
  IF v_status NOT IN ('SUBMITTED', 'WAITING') THEN
    ROLLBACK; SET p_result = 'INVALID'; LEAVE alloc_main;          -- already running / finished / cancelled
  END IF;
  IF v_status = 'SUBMITTED' THEN
    UPDATE jobs SET status = 'WAITING' WHERE id = p_job_id;  -- jobs inserted by the REST API start as SUBMITTED
  END IF;

  -- 3a. availability (admission control)
  SELECT allocated INTO u_cpu FROM v_resource_usage WHERE resource_type = 'CPU';
  SELECT allocated INTO u_ram FROM v_resource_usage WHERE resource_type = 'RAM';
  SELECT allocated INTO u_sto FROM v_resource_usage WHERE resource_type = 'STORAGE';

  IF r_cpu > t_cpu - u_cpu OR r_ram > t_ram - u_ram OR r_sto > t_sto - u_sto THEN
    COMMIT; SET p_result = 'DEFERRED_UNAVAILABLE'; LEAVE alloc_main;   -- stays WAITING
  END IF;

  -- 3b. Banker's safe-state check
  CALL sp_bankers_check(p_job_id, v_safe);
  IF v_safe = 0 THEN
    COMMIT; SET p_result = 'DEFERRED_UNSAFE'; LEAVE alloc_main;        -- stays WAITING
  END IF;

  -- 3c. grant: one allocation row per resource, then start the job
  INSERT INTO allocations (job_id, resource_id, amount)
VALUES (p_job_id, id_cpu, r_cpu);

INSERT INTO allocations (job_id, resource_id, amount)
VALUES (p_job_id, id_ram, r_ram);

INSERT INTO allocations (job_id, resource_id, amount)
VALUES (p_job_id, id_sto, r_sto);
  UPDATE jobs SET status = 'RUNNING' WHERE id = p_job_id;        -- trigger logs the change

  COMMIT;
  SET p_result = 'GRANTED';
END$$

-- ---------------------------------------------------------------------------------------------
-- sp_release_job: finish a RUNNING job (COMPLETED or FAILED). The AFTER UPDATE trigger releases the
-- allocations, marks the container and logs the change; this procedure only wraps it in one transaction.
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_release_job$$
CREATE PROCEDURE sp_release_job(IN p_job_id INT, IN p_final_status VARCHAR(20))
BEGIN
  DECLARE v_status VARCHAR(20) DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  IF p_final_status NOT IN ('COMPLETED', 'FAILED') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Final status must be COMPLETED or FAILED';
  END IF;

  START TRANSACTION;
  SELECT status INTO v_status FROM jobs WHERE id = p_job_id FOR UPDATE;
  IF v_status IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Job not found';
  END IF;
  UPDATE jobs SET status = p_final_status WHERE id = p_job_id;   -- trigger rejects it unless job is RUNNING
  COMMIT;
END$$

-- ---------------------------------------------------------------------------------------------
-- sp_dispatch_waiting: after resources are released, start waiting jobs in policy order.
--   FCFS (arrival), SJF (est_time), PRIORITY, PRIORITY_AGING (priority improves by 1 per p_aging_seconds waited).
--   Mirrors cluster_sim.py: FCFS/SJF/PRIORITY backfill (skip a job that does not fit and try the next);
--   PRIORITY_AGING reserves: if the top-ranked job cannot start, nothing behind it is started.
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_dispatch_waiting$$
CREATE PROCEDURE sp_dispatch_waiting(IN p_policy VARCHAR(20), IN p_aging_seconds INT, OUT p_started INT)
BEGIN
  DECLARE v_n INT DEFAULT 0;
  DECLARE v_i INT DEFAULT 1;
  DECLARE v_job INT;
  DECLARE v_res VARCHAR(30);

  IF p_policy NOT IN ('FCFS', 'SJF', 'PRIORITY', 'PRIORITY_AGING') THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unknown policy';
  END IF;
  IF p_aging_seconds IS NULL OR p_aging_seconds <= 0 THEN
    SET p_aging_seconds = 60;
  END IF;
  SET p_started = 0;

  DROP TEMPORARY TABLE IF EXISTS tmp_queue;
  CREATE TEMPORARY TABLE tmp_queue (pos INT AUTO_INCREMENT PRIMARY KEY, job_id INT NOT NULL);
  INSERT INTO tmp_queue (job_id)
  SELECT q.job_id
  FROM v_waiting_queue q
  ORDER BY CASE p_policy
             WHEN 'FCFS'           THEN q.arrival_time
             WHEN 'SJF'            THEN q.est_time
             WHEN 'PRIORITY'       THEN q.priority
             WHEN 'PRIORITY_AGING' THEN GREATEST(1, q.priority - FLOOR(q.waited_seconds / p_aging_seconds))
           END,
           q.arrival_time, q.job_id;
  SELECT COUNT(*) INTO v_n FROM tmp_queue;

  dispatch_loop: WHILE v_i <= v_n DO
    SELECT job_id INTO v_job FROM tmp_queue WHERE pos = v_i;
    CALL sp_allocate_job(v_job, v_res);
    IF v_res = 'GRANTED' THEN
      SET p_started = p_started + 1;
    ELSEIF p_policy = 'PRIORITY_AGING' THEN
      LEAVE dispatch_loop;                       -- reservation: do not let smaller jobs bypass the aged top job
    END IF;
    SET v_i = v_i + 1;
  END WHILE;

  DROP TEMPORARY TABLE tmp_queue;
END$$

-- ---------------------------------------------------------------------------------------------
-- sp_place_job_on_vm: best-fit placement of a RUNNING job into the RUNNING VM with the least free vCPU that
-- still fits it, then creates the container row. The container trigger re-checks capacity under a row lock.
-- p_vm_id is NULL when no VM has room (job keeps its host allocation; caller may retry later).
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_place_job_on_vm$$
CREATE PROCEDURE sp_place_job_on_vm(IN p_job_id INT, OUT p_vm_id INT)
BEGIN
  DECLARE v_cpu, v_ram INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  SELECT cpu_cores, ram_mb INTO v_cpu, v_ram FROM jobs WHERE id = p_job_id;
  SET p_vm_id = NULL;

  SELECT vm_id INTO p_vm_id FROM v_vm_status
  WHERE status = 'RUNNING' AND free_vcpu >= v_cpu AND free_vram_mb >= v_ram
  ORDER BY free_vcpu, free_vram_mb, vm_id LIMIT 1;

  IF p_vm_id IS NOT NULL THEN
    START TRANSACTION;
    INSERT INTO containers (job_id, vm_id, cpu_limit, mem_limit_mb, status)
    VALUES (p_job_id, p_vm_id, v_cpu, v_ram, 'CREATED');
    COMMIT;
  END IF;
END$$

-- ---------------------------------------------------------------------------------------------
-- sp_mark_recommended: after the Python adaptive scheduler stored one score per algorithm, flag the lowest score.
-- Ties are broken alphabetically (same as adaptive_scheduler.evaluate). Unique index guarantees one winner.
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_mark_recommended$$
CREATE PROCEDURE sp_mark_recommended(IN p_comparison_id INT)
BEGIN
  DECLARE v_best INT DEFAULT NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  START TRANSACTION;
  SELECT id INTO v_best FROM algorithm_results
  WHERE comparison_id = p_comparison_id
  ORDER BY score, CAST(algorithm AS CHAR) LIMIT 1;
  IF v_best IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'No results stored for this comparison';
  END IF;
  UPDATE algorithm_results SET is_recommended = 0 WHERE comparison_id = p_comparison_id;
  UPDATE algorithm_results SET is_recommended = 1 WHERE id = v_best;
  COMMIT;
END$$

-- ---------------------------------------------------------------------------------------------
-- sp_assert_vm_capacity: used by the VM triggers. The sum of all non-destroyed VMs must fit on the physical host.
-- p_exclude_vm = id of the VM being updated (so it is not counted twice), 0 for a new VM.
-- ---------------------------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_assert_vm_capacity$$
CREATE PROCEDURE sp_assert_vm_capacity(IN p_exclude_vm INT, IN p_vcpu INT, IN p_vram INT, IN p_vdisk INT)
BEGIN
  DECLARE t_cpu, t_ram, t_sto INT;
  DECLARE s_cpu, s_ram, s_sto BIGINT;
  SELECT total_capacity INTO t_cpu FROM resources WHERE resource_type = 'CPU';
  SELECT total_capacity INTO t_ram FROM resources WHERE resource_type = 'RAM';
  SELECT total_capacity INTO t_sto FROM resources WHERE resource_type = 'STORAGE';
  SELECT COALESCE(SUM(vcpu), 0), COALESCE(SUM(vram_mb), 0), COALESCE(SUM(vdisk_mb), 0)
    INTO s_cpu, s_ram, s_sto
  FROM vms WHERE status <> 'DESTROYED' AND id <> p_exclude_vm;
  IF s_cpu + p_vcpu > t_cpu OR s_ram + p_vram > t_ram OR s_sto + p_vdisk > t_sto THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'VMs would exceed the physical host capacity';
  END IF;
END$$

DELIMITER ;
