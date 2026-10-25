-- =====================================================================================
-- CloudSched  |  04_triggers.sql  |  Triggers (automatic rules enforced by the database itself)
-- Needs the mysql client / Workbench (DELIMITER).  Requires 03_procedures.sql (sp_assert_vm_capacity).
-- =====================================================================================
USE cloudsched;
DELIMITER $$

-- ------------------------------------------------------------------ jobs
-- T1  Lifecycle state machine. Same rules as ALLOWED in app/services/job_service.py, but enforced for EVERY writer
--     (REST API, procedures, a person typing UPDATE in Workbench).
DROP TRIGGER IF EXISTS trg_jobs_bu_status_guard$$
CREATE TRIGGER trg_jobs_bu_status_guard BEFORE UPDATE ON jobs
FOR EACH ROW
BEGIN
  IF NEW.status <> OLD.status THEN
    IF NOT (   (OLD.status = 'SUBMITTED' AND NEW.status IN ('WAITING', 'CANCELLED'))
            OR (OLD.status = 'WAITING'   AND NEW.status IN ('RUNNING', 'CANCELLED'))
            OR (OLD.status = 'RUNNING'   AND NEW.status IN ('COMPLETED', 'FAILED'))) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid job status transition';
    END IF;
  END IF;
END$$

-- T2  New job: log the first status and create its Banker's Max row for every resource (max = the request).
DROP TRIGGER IF EXISTS trg_jobs_ai_init$$
CREATE TRIGGER trg_jobs_ai_init AFTER INSERT ON jobs
FOR EACH ROW
BEGIN
  INSERT INTO execution_history (job_id, old_status, new_status) VALUES (NEW.id, NULL, NEW.status);
  INSERT INTO banker_matrices (job_id, resource_id, max_demand)
  SELECT NEW.id, r.id,
         CASE r.resource_type WHEN 'CPU' THEN NEW.cpu_cores WHEN 'RAM' THEN NEW.ram_mb ELSE NEW.storage_mb END
  FROM resources r;
END$$

-- T3  Every status change is logged; when a RUNNING job ends, its resources are released automatically
--     (released_at is stamped once: the WHERE released_at IS NULL makes a second release a no-op) and its container is closed.
DROP TRIGGER IF EXISTS trg_jobs_au_log_release$$
CREATE TRIGGER trg_jobs_au_log_release AFTER UPDATE ON jobs
FOR EACH ROW
BEGIN
  IF NEW.status <> OLD.status THEN
    INSERT INTO execution_history (job_id, old_status, new_status) VALUES (NEW.id, OLD.status, NEW.status);

    IF OLD.status = 'RUNNING' AND NEW.status IN ('COMPLETED', 'FAILED') THEN
      UPDATE allocations SET released_at = NOW() WHERE job_id = NEW.id AND released_at IS NULL;
      UPDATE containers
         SET status = IF(NEW.status = 'COMPLETED', 'EXITED', 'FAILED'), finished_at = NOW()
       WHERE job_id = NEW.id AND status IN ('CREATED', 'RUNNING');
    END IF;
  END IF;
END$$

-- T4  A RUNNING job holds resources: it cannot be deleted.
DROP TRIGGER IF EXISTS trg_jobs_bd_protect_running$$
CREATE TRIGGER trg_jobs_bd_protect_running BEFORE DELETE ON jobs
FOR EACH ROW
BEGIN
  IF OLD.status = 'RUNNING' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Cannot delete a RUNNING job';
  END IF;
END$$

-- ------------------------------------------------------------------ allocations
-- T5  Defence in depth. sp_allocate_job already checks all of this; the trigger protects against anybody who
--     INSERTs into allocations directly: job must be WAITING, amount <= Banker's Max, and total allocated <= capacity.
--     It takes the resource row lock itself, so even direct inserts are serialised per resource.
DROP TRIGGER IF EXISTS trg_alloc_bi_guard$$
CREATE TRIGGER trg_alloc_bi_guard BEFORE INSERT ON allocations
FOR EACH ROW
BEGIN
  DECLARE v_status VARCHAR(20) DEFAULT NULL;
  DECLARE v_cap INT DEFAULT NULL;
  DECLARE v_used BIGINT DEFAULT 0;
  DECLARE v_max INT DEFAULT NULL;

  SELECT status INTO v_status FROM jobs WHERE id = NEW.job_id;
  IF v_status IS NULL OR v_status <> 'WAITING' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Resources can only be allocated to a WAITING job';
  END IF;

  SELECT max_demand INTO v_max FROM banker_matrices WHERE job_id = NEW.job_id AND resource_id = NEW.resource_id;
  IF v_max IS NULL OR NEW.amount > v_max THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Allocation exceeds the declared maximum demand (Banker rule)';
  END IF;

  SELECT total_capacity INTO v_cap FROM resources WHERE id = NEW.resource_id;
  SELECT COALESCE(SUM(amount), 0) INTO v_used FROM allocations WHERE resource_id = NEW.resource_id AND released_at IS NULL;
  IF v_used + NEW.amount > v_cap THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Over-allocation: not enough free resource';
  END IF;
END$$

-- ------------------------------------------------------------------ vms (hypervisor rules)
-- T6  A new VM must fit on the physical host together with the existing VMs.
DROP TRIGGER IF EXISTS trg_vms_bi_capacity$$
CREATE TRIGGER trg_vms_bi_capacity BEFORE INSERT ON vms
FOR EACH ROW
BEGIN
  IF NEW.status <> 'DESTROYED' THEN
    CALL sp_assert_vm_capacity(0, NEW.vcpu, NEW.vram_mb, NEW.vdisk_mb);
  END IF;
END$$

-- T7  VM lifecycle: CREATED -> RUNNING <-> STOPPED, (CREATED|STOPPED) -> DESTROYED (final). A VM that still hosts
--     active containers cannot be stopped or destroyed. Resizing re-checks the host capacity.
DROP TRIGGER IF EXISTS trg_vms_bu_lifecycle$$
CREATE TRIGGER trg_vms_bu_lifecycle BEFORE UPDATE ON vms
FOR EACH ROW
BEGIN
  DECLARE v_active INT DEFAULT 0;

  IF NEW.status <> OLD.status THEN
    IF NOT (   (OLD.status = 'CREATED' AND NEW.status IN ('RUNNING', 'DESTROYED'))
            OR (OLD.status = 'RUNNING' AND NEW.status = 'STOPPED')
            OR (OLD.status = 'STOPPED' AND NEW.status IN ('RUNNING', 'DESTROYED'))) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid VM status transition';
    END IF;
    IF NEW.status IN ('STOPPED', 'DESTROYED') THEN
      SELECT COUNT(*) INTO v_active FROM containers WHERE vm_id = OLD.id AND status IN ('CREATED', 'RUNNING');
      IF v_active > 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'VM still has active containers';
      END IF;
    END IF;
  END IF;

  IF NEW.status <> 'DESTROYED' AND (NEW.vcpu <> OLD.vcpu OR NEW.vram_mb <> OLD.vram_mb OR NEW.vdisk_mb <> OLD.vdisk_mb) THEN
    CALL sp_assert_vm_capacity(OLD.id, NEW.vcpu, NEW.vram_mb, NEW.vdisk_mb);
  END IF;
END$$

-- ------------------------------------------------------------------ containers
-- T8  A container may only be created for a RUNNING job, inside a RUNNING VM that still has free vCPU and RAM.
--     The VM row is locked so two containers cannot both claim the last free share.
DROP TRIGGER IF EXISTS trg_cont_bi_capacity$$
CREATE TRIGGER trg_cont_bi_capacity BEFORE INSERT ON containers
FOR EACH ROW
BEGIN
  DECLARE v_job_status VARCHAR(20) DEFAULT NULL;
  DECLARE v_vm_status VARCHAR(20) DEFAULT NULL;
  DECLARE v_vcpu, v_vram INT DEFAULT 0;
  DECLARE v_used_cpu, v_used_ram BIGINT DEFAULT 0;

  SELECT status INTO v_job_status FROM jobs WHERE id = NEW.job_id;
  IF v_job_status IS NULL OR v_job_status <> 'RUNNING' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Containers can only be created for RUNNING jobs';
  END IF;

  SELECT status, vcpu, vram_mb INTO v_vm_status, v_vcpu, v_vram FROM vms WHERE id = NEW.vm_id FOR UPDATE;
  IF v_vm_status IS NULL OR v_vm_status <> 'RUNNING' THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'VM is not RUNNING';
  END IF;

  SELECT COALESCE(SUM(cpu_limit), 0), COALESCE(SUM(mem_limit_mb), 0) INTO v_used_cpu, v_used_ram
  FROM containers WHERE vm_id = NEW.vm_id AND status IN ('CREATED', 'RUNNING');
  IF v_used_cpu + NEW.cpu_limit > v_vcpu OR v_used_ram + NEW.mem_limit_mb > v_vram THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'VM has not enough free vCPU/RAM for this container';
  END IF;
END$$

DELIMITER ;
