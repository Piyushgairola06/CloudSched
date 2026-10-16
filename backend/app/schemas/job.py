from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.job import JobStatus, JobType


class JobCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    cpu_cores: int = Field(gt=0, le=1024)
    ram_mb: int = Field(gt=0)
    storage_mb: int = Field(default=0, ge=0)
    est_time: int = Field(gt=0, le=100000)
    priority: int = Field(default=5, ge=1, le=10, description="1 = highest priority")
    arrival_time: int = Field(default=0, ge=0)
    deadline: int | None = Field(default=None, gt=0)
    job_type: JobType = JobType.CPU_BOUND

    @model_validator(mode="after")
    def deadline_feasible(self):
        if self.deadline is not None and self.deadline < self.arrival_time + self.est_time:
            raise ValueError("deadline is impossible: must be >= arrival_time + est_time")
        return self


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None
    cpu_cores: int
    ram_mb: int
    storage_mb: int
    est_time: int
    priority: int
    arrival_time: int
    deadline: int | None
    job_type: JobType
    status: JobStatus
    created_at: datetime
    updated_at: datetime
