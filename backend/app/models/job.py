import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class JobStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    WAITING = "WAITING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobType(str, enum.Enum):
    # Each maps to a predefined safe workload later (Phase E); never user-supplied code.
    CPU_BOUND = "CPU_BOUND"
    MEMORY_BOUND = "MEMORY_BOUND"
    IO_BOUND = "IO_BOUND"
    MIXED = "MIXED"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("cpu_cores > 0", name="ck_jobs_cpu_pos"),
        CheckConstraint("ram_mb > 0", name="ck_jobs_ram_pos"),
        CheckConstraint("storage_mb >= 0", name="ck_jobs_storage_nonneg"),
        CheckConstraint("est_time > 0", name="ck_jobs_est_pos"),
        CheckConstraint("priority BETWEEN 1 AND 10", name="ck_jobs_priority_range"),
        CheckConstraint("arrival_time >= 0", name="ck_jobs_arrival_nonneg"),
        CheckConstraint("deadline IS NULL OR deadline > arrival_time", name="ck_jobs_deadline"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_arrival", "arrival_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    est_time: Mapped[int] = mapped_column(Integer, nullable=False)  # burst time, simulation time units
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)  # 1 = HIGHEST, 10 = lowest
    arrival_time: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # sim time units
    deadline: Mapped[int | None] = mapped_column(Integer)  # absolute sim time, optional
    job_type: Mapped[JobType] = mapped_column(Enum(JobType), nullable=False, default=JobType.CPU_BOUND)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), nullable=False, default=JobStatus.SUBMITTED)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
