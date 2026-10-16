from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.job import JobStatus
from app.repositories import job_repo
from app.schemas.job import JobCreate, JobOut
from app.services import job_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
def create(data: JobCreate, db: Session = Depends(get_db)):
    return job_service.create_job(db, data)


@router.get("", response_model=list[JobOut])
def list_all(status: JobStatus | None = None, limit: int = Query(100, ge=1, le=500),
             offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return job_repo.list_jobs(db, status, limit, offset)


@router.get("/{job_id}", response_model=JobOut)
def get_one(job_id: int, db: Session = Depends(get_db)):
    return job_service.get_job(db, job_id)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel(job_id: int, db: Session = Depends(get_db)):
    return job_service.cancel_job(db, job_id)
