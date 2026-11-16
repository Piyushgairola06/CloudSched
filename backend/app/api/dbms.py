from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.database.session import get_db

router = APIRouter(prefix="/api/dbms", tags=["DBMS dashboard"])


def rows(db: Session, sql: str):
    return [dict(row._mapping) for row in db.execute(text(sql)).all()]


@router.get("/resources")
def resources(db: Session = Depends(get_db)):
    return rows(db, "SELECT * FROM v_resource_usage ORDER BY resource_id")


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return {
        "jobs_by_status": rows(db, "SELECT * FROM v_job_status_summary ORDER BY status"),
        "resources": rows(db, "SELECT * FROM v_resource_usage ORDER BY resource_id"),
        "vms": rows(db, "SELECT * FROM v_vm_status ORDER BY vm_id"),
        "waiting_queue": rows(db, "SELECT * FROM v_waiting_queue ORDER BY priority, arrival_time, job_id"),
        "recent_history": rows(db, "SELECT id, job_id, old_status, new_status, changed_at FROM execution_history ORDER BY id DESC LIMIT 30"),
    }


@router.get("/running")
def running(db: Session = Depends(get_db)):
    return rows(db, "SELECT * FROM v_running_jobs ORDER BY job_id")


@router.get("/containers")
def containers(db: Session = Depends(get_db)):
    return rows(db, "SELECT * FROM v_container_status ORDER BY container_id DESC LIMIT 50")


@router.get("/vms")
def vms(db: Session = Depends(get_db)):
    return rows(db, "SELECT * FROM v_vm_status ORDER BY vm_id")


@router.get("/history/{job_id}")
def history(job_id: int, db: Session = Depends(get_db)):
    result = db.execute(text("SELECT id, job_id, old_status, new_status, changed_at FROM execution_history WHERE job_id=:job_id ORDER BY changed_at, id"), {"job_id": job_id})
    return [dict(row._mapping) for row in result.all()]


@router.get("/analytics")
def analytics(db: Session = Depends(get_db)):
    return {
        "job_metrics": rows(db, "SELECT * FROM v_job_metrics ORDER BY job_id, algorithm"),
        "algorithm_comparison": rows(db, "SELECT * FROM v_algorithm_comparison ORDER BY comparison_id DESC, score_rank"),
        "users": rows(db, "SELECT * FROM v_user_job_summary ORDER BY user_id"),
    }
