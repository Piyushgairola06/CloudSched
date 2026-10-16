from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus


def add(db: Session, job: Job) -> Job:
    db.add(job)
    db.flush()  # assigns id, stays inside caller's transaction
    return job


def get(db: Session, job_id: int, for_update: bool = False) -> Job | None:
    stmt = select(Job).where(Job.id == job_id)
    if for_update:
        stmt = stmt.with_for_update()  # row lock on MySQL/InnoDB; ignored by SQLite
    return db.execute(stmt).scalar_one_or_none()


def list_jobs(db: Session, status: JobStatus | None = None, limit: int = 100, offset: int = 0) -> list[Job]:
    stmt = select(Job).order_by(Job.id).limit(limit).offset(offset)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    return list(db.execute(stmt).scalars())
