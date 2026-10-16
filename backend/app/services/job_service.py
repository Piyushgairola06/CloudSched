"""Job management. Transaction boundary: each public function commits or rolls back as a unit."""
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import Job, JobStatus
from app.repositories import job_repo
from app.schemas.job import JobCreate


class JobError(Exception):
    status_code = 400


class JobNotFound(JobError):
    status_code = 404


class InvalidTransition(JobError):
    status_code = 409


class ImpossibleRequest(JobError):
    status_code = 422


# Allowed lifecycle moves. Anything not listed is rejected.
ALLOWED = {
    JobStatus.SUBMITTED: {JobStatus.WAITING, JobStatus.CANCELLED},
    JobStatus.WAITING: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.COMPLETED, JobStatus.FAILED},
    JobStatus.COMPLETED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


def check_transition(current: JobStatus, new: JobStatus) -> None:
    if new not in ALLOWED[current]:
        raise InvalidTransition(f"Cannot move job from {current.value} to {new.value}")


def create_job(db: Session, data: JobCreate) -> Job:
    # Reject requests that can never fit on the host, instead of queueing them forever.
    if data.cpu_cores > settings.host_total_cpu:
        raise ImpossibleRequest(f"cpu_cores {data.cpu_cores} exceeds host total {settings.host_total_cpu}")
    if data.ram_mb > settings.host_total_ram_mb:
        raise ImpossibleRequest(f"ram_mb {data.ram_mb} exceeds host total {settings.host_total_ram_mb}")
    if data.storage_mb > settings.host_total_storage_mb:
        raise ImpossibleRequest(f"storage_mb {data.storage_mb} exceeds host total {settings.host_total_storage_mb}")
    try:
        job = job_repo.add(db, Job(**data.model_dump()))
        db.commit()
        return job
    except Exception:
        db.rollback()
        raise


def get_job(db: Session, job_id: int) -> Job:
    job = job_repo.get(db, job_id)
    if job is None:
        raise JobNotFound(f"Job {job_id} not found")
    return job


def cancel_job(db: Session, job_id: int) -> Job:
    try:
        job = job_repo.get(db, job_id, for_update=True)  # lock so cancel can't race a scheduler start
        if job is None:
            raise JobNotFound(f"Job {job_id} not found")
        check_transition(job.status, JobStatus.CANCELLED)  # RUNNING cannot be cancelled in Phase A
        job.status = JobStatus.CANCELLED
        db.commit()
        return job
    except Exception:
        db.rollback()
        raise
