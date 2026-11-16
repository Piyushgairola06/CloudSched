"""OS <-> DBMS bridge endpoints (these need MySQL)."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.services import bankers_algorithm as bk
from app.services import os_lab
from app.services.automation import tick
from app.services.bridge import db_algorithm, recommend_policy
from app.services.executor import get_executor
from app.core.config import settings

router = APIRouter(prefix="/api/bridge", tags=["OS + DBMS bridge"])


def _live_recommendation(db: Session):
    queue = [dict(r._mapping) for r in db.execute(text(
        "SELECT job_id, priority, arrival_time, est_time, cpu_cores, ram_mb, storage_mb FROM v_waiting_queue "
        "ORDER BY job_id"))]
    cap = {r.resource_type: int(r.total_capacity) for r in db.execute(text("SELECT resource_type, total_capacity FROM resources"))}
    if not cap:
        raise HTTPException(status_code=409, detail="resources table is empty: install database/05_seed_data.sql")
    return recommend_policy(queue, cap["CPU"], cap["RAM"], cap["STORAGE"], aging_interval=10)


@router.get("/recommend-policy")
def get_recommendation(db: Session = Depends(get_db)):
    """Replay the CURRENT waiting queue through the OS simulator under every dispatch policy and rank them."""
    try:
        return _live_recommendation(db)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/apply-recommended")
def apply_recommended(db: Session = Depends(get_db)):
    """Dispatch the waiting queue with the policy the OS simulator recommends, then place + launch the started jobs."""
    rec = _live_recommendation(db)
    if rec["recommended"] is None:
        return {"recommendation": rec, "result": None}
    try:
        result = tick(db, get_executor(), rec["recommended"], settings.aging_seconds, reap=False)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"recommendation": rec, "result": result}


@router.post("/compare")
def compare_and_store(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Run the adaptive scheduler (same JSON as /api/os/recommend) and STORE it in comparison_runs/algorithm_results."""
    try:
        res = os_lab.recommend(payload)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing field: {exc}") from exc
    except (ValueError, TypeError, AssertionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        db.execute(text("INSERT INTO comparison_runs (requested_by, time_quantum, job_count) VALUES (NULL, :q, :n)"),
                   {"q": int(payload.get("quantum", 4)), "n": res["workload"]["job_count"]})
        cid = int(db.execute(text("SELECT LAST_INSERT_ID()")).scalar_one())
        for name, m in res["raw"].items():
            db.execute(text(
                "INSERT INTO algorithm_results (comparison_id, algorithm, avg_waiting, avg_turnaround, avg_response, "
                "max_waiting, cpu_util_pct, throughput, score) VALUES (:c, :a, :w, :t, :r, :mw, :u, :th, :s)"),
                {"c": cid, "a": db_algorithm(name), "w": m["avg_waiting"], "t": m["avg_turnaround"],
                 "r": m["avg_response"], "mw": m["max_waiting"], "u": round(100 * m["cpu_utilization"], 2),
                 "th": round(m["throughput"], 4), "s": res["scores"][name]})
        db.commit()
        db.execute(text("CALL sp_mark_recommended(:c)"), {"c": cid})   # procedure owns its own transaction
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"could not store comparison: {exc}") from exc
    return {"comparison_id": cid, **res}


@router.get("/banker/{job_id}")
def banker_crosscheck(job_id: int, db: Session = Depends(get_db)):
    """Ask the Python Banker's algorithm AND the SQL procedure the same question about a WAITING job; they must agree."""
    status = db.execute(text("SELECT status FROM jobs WHERE id = :j"), {"j": job_id}).scalar()
    if status is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if status != "WAITING":
        raise HTTPException(status_code=409, detail=f"job is {status}; the check needs a WAITING job")
    kinds = ("CPU", "RAM", "STORAGE")
    mx, al = {}, {}
    for r in db.execute(text("SELECT job_id, resource_type, max_demand, allocation FROM v_banker_matrices")):
        mx.setdefault(r.job_id, [0, 0, 0])[kinds.index(r.resource_type)] = int(r.max_demand)
        al.setdefault(r.job_id, [0, 0, 0])[kinds.index(r.resource_type)] = int(r.allocation)
    avail = [0, 0, 0]
    for r in db.execute(text("SELECT resource_type, available FROM v_resource_usage")):
        avail[kinds.index(r.resource_type)] = int(r.available)
    job = db.execute(text("SELECT cpu_cores, ram_mb, storage_mb FROM jobs WHERE id = :j"), {"j": job_id}).one()
    dec = bk.request_resources(job_id, [job.cpu_cores, job.ram_mb, job.storage_mb], avail, mx, al)
    db.execute(text("CALL sp_bankers_check(:j, @safe)"), {"j": job_id})
    sql_safe = bool(db.execute(text("SELECT @safe")).scalar_one())
    py_safe = dec.status == bk.GRANTED
    return {"job_id": job_id, "python": {"status": dec.status, "reason": dec.reason, "safe_sequence": dec.safe_sequence},
            "sql_safe": sql_safe, "agree": py_safe == sql_safe,
            "state": {"available": avail, "max": mx, "allocation": al}}
