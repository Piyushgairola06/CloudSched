from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.job import JobStatus
from app.repositories import job_repo
from app.schemas.job import JobCreate, JobOut
from app.services import job_service
from app.services.automation import cleanup_job_container
from app.services.mysql_procedures import submit_job, allocate_job, release_job, dispatch_waiting, place_job_on_vm

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("", response_model=JobOut, status_code=201)
def create(data: JobCreate, db: Session = Depends(get_db)):
    """Create a job through MySQL admission control; triggers create history and Banker rows."""
    try:
        job_id = submit_job(
            db, user_id=None, name=data.name, cpu=data.cpu_cores, ram_mb=data.ram_mb,
            storage_mb=data.storage_mb, est_time=data.est_time, priority=data.priority,
            arrival=data.arrival_time, deadline=data.deadline, job_type=data.job_type.value,
        )
        if data.description:
            db.execute(text("UPDATE jobs SET description=:description WHERE id=:job_id"),
                       {"description": data.description, "job_id": job_id})
            db.commit()
        job = job_repo.get(db, job_id)
        if job is None:
            raise HTTPException(status_code=500, detail="MySQL created the job but it could not be reloaded")
        return job
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=f"MySQL rejected job submission: {exc}") from exc


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


@router.post("/{job_id}/allocate")
def allocate(job_id: int, db: Session = Depends(get_db)):
    """Run MySQL admission control + Banker's safety check; best-fit place in a VM if granted."""
    try:
        result = allocate_job(db, job_id)
        vm_id = place_job_on_vm(db, job_id) if result == "GRANTED" else None
        db.commit()
        return {"job_id": job_id, "result": result, "vm_id": vm_id,
                "message": ("Resources allocated; VM/container placement attempted." if result == "GRANTED"
                            else "Job remains in WAITING state until resources become available.")}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Allocation failed: {exc}") from exc


@router.post("/{job_id}/complete")
def complete(job_id: int, db: Session = Depends(get_db)):
    try:
        release_job(db, job_id, "COMPLETED")
        started = dispatch_waiting(db, "PRIORITY_AGING", 60)
        db.commit()
        cleanup_job_container(db, job_id)  # stop a real container if one is still running
        return {"job_id": job_id, "status": "COMPLETED", "waiting_jobs_started": started}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Could not complete job: {exc}") from exc


@router.post("/{job_id}/fail")
def fail(job_id: int, db: Session = Depends(get_db)):
    try:
        release_job(db, job_id, "FAILED")
        started = dispatch_waiting(db, "PRIORITY_AGING", 60)
        db.commit()
        cleanup_job_container(db, job_id)  # stop a real container if one is still running
        return {"job_id": job_id, "status": "FAILED", "waiting_jobs_started": started}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Could not fail job: {exc}") from exc


@router.post("/dispatch")
def dispatch(policy: str = Query("PRIORITY_AGING", pattern="^(FCFS|SJF|PRIORITY|PRIORITY_AGING)$"),
             aging_seconds: int = Query(60, ge=1), db: Session = Depends(get_db)):
    try:
        started = dispatch_waiting(db, policy, aging_seconds)
        db.commit()
        return {"policy": policy, "started_jobs": started}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Dispatch failed: {exc}") from exc
