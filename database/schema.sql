-- Phase A reference schema (MySQL 8). Later phases add host_resources, resource_allocations, etc.
CREATE DATABASE IF NOT EXISTS cloudsched CHARACTER SET utf8mb4;
USE cloudsched;

CREATE TABLE IF NOT EXISTS jobs (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  name        VARCHAR(100) NOT NULL,
  description TEXT NULL,
  cpu_cores   INT NOT NULL,
  ram_mb      INT NOT NULL,
  storage_mb  INT NOT NULL DEFAULT 0,
  est_time    INT NOT NULL,
  priority    INT NOT NULL DEFAULT 5,          -- 1 = highest
  arrival_time INT NOT NULL DEFAULT 0,
  deadline    INT NULL,
  job_type    ENUM('CPU_BOUND','MEMORY_BOUND','IO_BOUND','MIXED') NOT NULL DEFAULT 'CPU_BOUND',
  status      ENUM('SUBMITTED','WAITING','RUNNING','COMPLETED','FAILED','CANCELLED') NOT NULL DEFAULT 'SUBMITTED',
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT ck_jobs_cpu_pos CHECK (cpu_cores > 0),
  CONSTRAINT ck_jobs_ram_pos CHECK (ram_mb > 0),
  CONSTRAINT ck_jobs_storage_nonneg CHECK (storage_mb >= 0),
  CONSTRAINT ck_jobs_est_pos CHECK (est_time > 0),
  CONSTRAINT ck_jobs_priority_range CHECK (priority BETWEEN 1 AND 10),
  CONSTRAINT ck_jobs_arrival_nonneg CHECK (arrival_time >= 0),
  CONSTRAINT ck_jobs_deadline CHECK (deadline IS NULL OR deadline > arrival_time),
  INDEX ix_jobs_status (status),
  INDEX ix_jobs_arrival (arrival_time)
) ENGINE=InnoDB;
