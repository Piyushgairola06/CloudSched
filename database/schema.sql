-- =====================================================================================
-- CloudSched  |  01_schema.sql  |  DDL: tables, keys, constraints, indexes   (MySQL 8.0.16+)
-- Normalised to 3NF (see docs/DBMS_DESIGN.md).  CHECK constraints are only enforced from 8.0.16.
-- Run order: 01_schema -> 02_views -> 03_procedures -> 04_triggers -> 05_seed_data
-- WARNING: the DROP section below is a DEV RESET. Re-running this file deletes all data.
-- =====================================================================================
CREATE DATABASE IF NOT EXISTS cloudsched CHARACTER SET utf8mb4;
USE cloudsched;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS algorithm_results, comparison_runs, banker_matrices, job_executions,
                     containers, vms, execution_history, allocations, jobs, resources, users;
SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------- users
CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(50)  NOT NULL,
  email         VARCHAR(100) NOT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role          ENUM('ADMIN','USER') NOT NULL DEFAULT 'USER',
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_users_username UNIQUE (username),
  CONSTRAINT uq_users_email    UNIQUE (email),
  CONSTRAINT ck_users_email    CHECK (email LIKE '%_@_%.__%')
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- resources (the physical host)
-- One row per resource type. "allocated" and "available" are NOT stored (derived from allocations,
-- see v_resource_usage) so they can never drift out of sync.
CREATE TABLE resources (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  resource_type  ENUM('CPU','RAM','STORAGE') NOT NULL,
  total_capacity INT NOT NULL,
  unit           VARCHAR(10) NOT NULL,
  CONSTRAINT uq_resources_type UNIQUE (resource_type),
  CONSTRAINT ck_resources_cap  CHECK (total_capacity > 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- jobs (same columns as the SQLAlchemy model + user_id)
CREATE TABLE jobs (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  user_id      INT NULL,                                   -- NULL allowed: the Phase A API creates anonymous jobs
  name         VARCHAR(100) NOT NULL,
  description  TEXT NULL,
  cpu_cores    INT NOT NULL,
  ram_mb       INT NOT NULL,
  storage_mb   INT NOT NULL DEFAULT 0,
  est_time     INT NOT NULL,                               -- burst time, simulation units
  priority     INT NOT NULL DEFAULT 5,                     -- 1 = highest
  arrival_time INT NOT NULL DEFAULT 0,
  deadline     INT NULL,
  job_type     ENUM('CPU_BOUND','MEMORY_BOUND','IO_BOUND','MIXED') NOT NULL DEFAULT 'CPU_BOUND',
  status       ENUM('SUBMITTED','WAITING','RUNNING','COMPLETED','FAILED','CANCELLED') NOT NULL DEFAULT 'SUBMITTED',
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_jobs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT ck_jobs_cpu_pos          CHECK (cpu_cores > 0),
  CONSTRAINT ck_jobs_ram_pos          CHECK (ram_mb > 0),
  CONSTRAINT ck_jobs_storage_nonneg   CHECK (storage_mb >= 0),
  CONSTRAINT ck_jobs_est_pos          CHECK (est_time > 0),
  CONSTRAINT ck_jobs_priority_range   CHECK (priority BETWEEN 1 AND 10),
  CONSTRAINT ck_jobs_arrival_nonneg   CHECK (arrival_time >= 0),
  CONSTRAINT ck_jobs_deadline         CHECK (deadline IS NULL OR deadline > arrival_time),
  INDEX ix_jobs_status (status),
  INDEX ix_jobs_arrival (arrival_time),
  INDEX ix_jobs_queue (status, priority, arrival_time)     -- ready-queue lookup: WHERE status='WAITING' ORDER BY priority, arrival
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- allocations (job x resource, one row each)
-- Active allocation = released_at IS NULL.  UNIQUE(job_id, resource_id) = one allocation per job (invariant I3);
-- released_at is set once by a trigger when the job finishes (invariant I4).
CREATE TABLE allocations (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  job_id       INT NOT NULL,
  resource_id  INT NOT NULL,
  amount       INT NOT NULL,
  allocated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  released_at  DATETIME NULL,
  CONSTRAINT fk_alloc_job      FOREIGN KEY (job_id)      REFERENCES jobs(id)      ON DELETE CASCADE,
  CONSTRAINT fk_alloc_resource FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE RESTRICT,
  CONSTRAINT uq_alloc_job_res  UNIQUE (job_id, resource_id),
  CONSTRAINT ck_alloc_amount   CHECK (amount >= 0),
  CONSTRAINT ck_alloc_release  CHECK (released_at IS NULL OR released_at >= allocated_at),
  INDEX ix_alloc_active (resource_id, released_at)         -- SUM(amount) of active allocations per resource
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- execution_history (written ONLY by triggers)
CREATE TABLE execution_history (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  job_id     INT NOT NULL,
  old_status ENUM('SUBMITTED','WAITING','RUNNING','COMPLETED','FAILED','CANCELLED') NULL,   -- NULL = job just created
  new_status ENUM('SUBMITTED','WAITING','RUNNING','COMPLETED','FAILED','CANCELLED') NOT NULL,
  changed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  CONSTRAINT fk_hist_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  INDEX ix_hist_job_time (job_id, changed_at)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- vms (hypervisor partitions of the host)
CREATE TABLE vms (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  name       VARCHAR(50) NOT NULL,
  vcpu       INT NOT NULL,
  vram_mb    INT NOT NULL,
  vdisk_mb   INT NOT NULL,
  status     ENUM('CREATED','RUNNING','STOPPED','DESTROYED') NOT NULL DEFAULT 'CREATED',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_vms_name UNIQUE (name),
  CONSTRAINT ck_vms_vcpu CHECK (vcpu > 0),
  CONSTRAINT ck_vms_vram CHECK (vram_mb > 0),
  CONSTRAINT ck_vms_vdisk CHECK (vdisk_mb > 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- containers (one Docker container per job)
-- No "image" column: the image is derived from jobs.job_type, storing it here would be a transitive dependency (3NF).
CREATE TABLE containers (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  docker_container_id VARCHAR(64) NULL,
  job_id              INT NOT NULL,
  vm_id               INT NOT NULL,
  cpu_limit           INT NOT NULL,
  mem_limit_mb        INT NOT NULL,
  status              ENUM('CREATED','RUNNING','EXITED','FAILED','REMOVED') NOT NULL DEFAULT 'CREATED',
  exit_code           INT NULL,
  started_at          DATETIME NULL,
  finished_at         DATETIME NULL,
  CONSTRAINT fk_cont_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  CONSTRAINT fk_cont_vm  FOREIGN KEY (vm_id)  REFERENCES vms(id)  ON DELETE RESTRICT,
  CONSTRAINT uq_cont_job    UNIQUE (job_id),
  CONSTRAINT uq_cont_docker UNIQUE (docker_container_id),
  CONSTRAINT ck_cont_cpu    CHECK (cpu_limit > 0),
  CONSTRAINT ck_cont_mem    CHECK (mem_limit_mb > 0),
  CONSTRAINT ck_cont_times  CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
  INDEX ix_cont_vm_status (vm_id, status)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- job_executions (Gantt segments of the executed schedule)
CREATE TABLE job_executions (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  job_id     INT NOT NULL,
  vm_id      INT NULL,
  algorithm  ENUM('FCFS','SJF','PRIORITY','RR','PRIORITY_AGING') NOT NULL,
  start_time INT NOT NULL,            -- simulation units
  end_time   INT NOT NULL,
  CONSTRAINT fk_exec_job FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  CONSTRAINT fk_exec_vm  FOREIGN KEY (vm_id)  REFERENCES vms(id)  ON DELETE SET NULL,
  CONSTRAINT ck_exec_time CHECK (start_time >= 0 AND end_time > start_time),
  INDEX ix_exec_job (job_id),
  INDEX ix_exec_algo (algorithm)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- banker_matrices (Max demand; Allocation/Need/Available are derived)
-- Max[job][resource] is declared at submission (defaults to the job's request, set by a trigger).
-- Allocation comes from `allocations`, Need = Max - Allocation, Available = total - allocated: all derived in
-- v_banker_matrices / v_resource_usage (storing them would be redundant derived data).
CREATE TABLE banker_matrices (
  job_id      INT NOT NULL,
  resource_id INT NOT NULL,
  max_demand  INT NOT NULL,
  PRIMARY KEY (job_id, resource_id),
  CONSTRAINT fk_bank_job FOREIGN KEY (job_id)      REFERENCES jobs(id)      ON DELETE CASCADE,
  CONSTRAINT fk_bank_res FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE RESTRICT,
  CONSTRAINT ck_bank_max CHECK (max_demand >= 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- adaptive scheduler: comparisons and their results
CREATE TABLE comparison_runs (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  requested_by INT NULL,
  time_quantum INT NOT NULL DEFAULT 4,
  job_count    INT NOT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_cmp_user FOREIGN KEY (requested_by) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT ck_cmp_quantum CHECK (time_quantum > 0),
  CONSTRAINT ck_cmp_jobs    CHECK (job_count > 0)
) ENGINE=InnoDB;

CREATE TABLE algorithm_results (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  comparison_id  INT NOT NULL,
  algorithm      ENUM('FCFS','SJF','PRIORITY','RR','PRIORITY_AGING') NOT NULL,
  avg_waiting    DECIMAL(10,2) NOT NULL,
  avg_turnaround DECIMAL(10,2) NOT NULL,
  avg_response   DECIMAL(10,2) NOT NULL,
  max_waiting    DECIMAL(10,2) NOT NULL,
  cpu_util_pct   DECIMAL(5,2)  NOT NULL,
  throughput     DECIMAL(10,4) NOT NULL,
  score          DECIMAL(8,4)  NOT NULL,                   -- weighted score, LOWER is better (adaptive_scheduler.py)
  is_recommended BOOLEAN NOT NULL DEFAULT 0,
  -- Trick: NULL for non-winners, 1 for the winner. UNIQUE(comparison_id, rec_marker) therefore allows
  -- at most ONE recommended algorithm per comparison (NULLs never collide in a UNIQUE index).
  rec_marker     TINYINT AS (IF(is_recommended, 1, NULL)) STORED,
  CONSTRAINT fk_res_cmp FOREIGN KEY (comparison_id) REFERENCES comparison_runs(id) ON DELETE CASCADE,
  CONSTRAINT uq_res_cmp_algo UNIQUE (comparison_id, algorithm),
  CONSTRAINT uq_res_one_winner UNIQUE (comparison_id, rec_marker),
  CONSTRAINT ck_res_util CHECK (cpu_util_pct BETWEEN 0 AND 100),
  CONSTRAINT ck_res_nonneg CHECK (avg_waiting >= 0 AND avg_turnaround >= 0 AND avg_response >= 0 AND max_waiting >= 0),
  INDEX ix_res_score (comparison_id, score)
) ENGINE=InnoDB;
