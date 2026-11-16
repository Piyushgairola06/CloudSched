"""Automation: one `tick()` = reap finished jobs -> dispatch waiting jobs -> place them in VMs -> launch containers.

This closes two gaps in the stored-procedure workflow:
  * sp_dispatch_waiting starts jobs (RUNNING + allocations) but does NOT place them in a VM or start anything;
  * nothing ever noticed that a job had finished.
`Automation` runs tick() on a background thread; the same tick can be fired manually (POST /api/automation/tick).
Every state change still goes through the stored procedures/triggers, so the DB rules (Banker's, row locks,
state machine) apply exactly as for manual API calls.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.session import SessionLocal
from app.services.executor import ExecJob, get_executor
from app.services.mysql_procedures import dispatch_waiting, place_job_on_vm, release_job

log = logging.getLogger("cloudsched.automation")
POLICIES = ("FCFS", "SJF", "PRIORITY", "PRIORITY_AGING")


def _reap(db: Session, ex) -> list[dict]:
    rows = db.execute(text(
        "SELECT c.id AS cid, c.job_id, c.docker_container_id AS ref, j.job_type, j.est_time, j.cpu_cores, j.ram_mb, "
        "       COALESCE(TIMESTAMPDIFF(SECOND, c.started_at, NOW()), 0) AS elapsed "
        "FROM containers c JOIN jobs j ON j.id = c.job_id "
        "WHERE c.status = 'RUNNING' AND j.status = 'RUNNING' ORDER BY c.id")).mappings().all()
    db.commit()
    finished = []
    for r in rows:
        job = ExecJob(r["job_id"], r["job_type"], r["cpu_cores"], r["ram_mb"], r["est_time"], float(r["elapsed"]))
        try:
            code = ex.poll(r["ref"], job)
            if code is None:
                continue
            db.execute(text("UPDATE containers SET exit_code = :c WHERE id = :id"), {"c": code, "id": r["cid"]})
            db.commit()
            final = "COMPLETED" if code == 0 else "FAILED"
            release_job(db, r["job_id"], final)       # trigger frees allocations + closes the container row
            db.commit()
            ex.cleanup(r["ref"])
            finished.append({"job_id": r["job_id"], "status": final, "exit_code": code})
        except Exception:
            db.rollback()
            log.exception("could not reap job %s", r["job_id"])
    return finished


def _place_unplaced(db: Session) -> list[dict]:
    ids = [r[0] for r in db.execute(text(
        "SELECT j.id FROM jobs j LEFT JOIN containers c ON c.job_id = j.id "
        "WHERE j.status = 'RUNNING' AND c.id IS NULL ORDER BY j.id"))]
    db.commit()
    placed = []
    for jid in ids:
        try:
            vm = place_job_on_vm(db, jid)             # best-fit; None when no VM has room (retried next tick)
            db.commit()
            if vm is not None:
                placed.append({"job_id": jid, "vm_id": vm})
        except Exception:
            db.rollback()
            log.exception("could not place job %s", jid)
    return placed


def _launch_created(db: Session, ex) -> list[dict]:
    rows = db.execute(text(
        "SELECT c.id AS cid, c.job_id, j.job_type, j.cpu_cores, j.ram_mb, j.est_time "
        "FROM containers c JOIN jobs j ON j.id = c.job_id "
        "WHERE c.status = 'CREATED' AND j.status = 'RUNNING' ORDER BY c.id")).mappings().all()
    db.commit()
    launched = []
    for r in rows:
        try:
            ref = ex.start(ExecJob(r["job_id"], r["job_type"], r["cpu_cores"], r["ram_mb"], r["est_time"]))
            db.execute(text("UPDATE containers SET docker_container_id = :d, status = 'RUNNING', started_at = NOW() "
                            "WHERE id = :id AND status = 'CREATED'"), {"d": ref, "id": r["cid"]})
            db.commit()
            launched.append({"job_id": r["job_id"], "container_id": r["cid"], "docker_id": ref})
        except Exception:
            db.rollback()                              # container stays CREATED -> retried on the next tick
            log.exception("could not launch job %s", r["job_id"])
            raise
    return launched


def tick(db: Session, ex, policy: Optional[str] = None, aging_seconds: int = 60, reap: bool = True) -> dict:
    out = {"finished": [], "dispatched": 0, "placed": [], "launched": []}
    if reap:
        out["finished"] = _reap(db, ex)
    if policy and policy in POLICIES:
        out["dispatched"] = dispatch_waiting(db, policy, aging_seconds)
        db.commit()
    out["placed"] = _place_unplaced(db)
    out["launched"] = _launch_created(db, ex)
    return out


def cleanup_job_container(db: Session, job_id: int) -> None:
    """After a MANUAL complete/fail: make sure a real container for that job does not keep running."""
    try:
        ref = db.execute(text("SELECT docker_container_id FROM containers WHERE job_id = :j"), {"j": job_id}).scalar()
        if ref:
            get_executor().cleanup(ref)
    except Exception:
        log.exception("container cleanup failed for job %s", job_id)


class Automation:
    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()             # a manual tick and the loop never overlap
        self.interval = settings.automation_interval
        self.policy: Optional[str] = None if settings.automation_policy.upper() == "NONE" else settings.automation_policy.upper()
        self.aging_seconds = settings.aging_seconds
        self.ticks = 0
        self.last_tick_at: Optional[str] = None
        self.last_result: Optional[dict] = None
        self.last_error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self, policy: Optional[str] = "__default__") -> dict:
        pol = self.policy if policy == "__default__" else policy
        with self._tick_lock:
            try:
                with SessionLocal() as db:
                    result = tick(db, get_executor(), pol, self.aging_seconds)
                self.last_result, self.last_error = result, None
                return result
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("automation tick failed")
                raise
            finally:
                self.ticks += 1
                self.last_tick_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.run_once()
            except Exception:
                pass                                   # already logged + stored in last_error

    def start(self, policy: Optional[str] = None, interval: Optional[float] = None) -> None:
        if policy is not None:
            pol = policy.upper()
            if pol != "NONE" and pol not in POLICIES:
                raise ValueError(f"policy must be one of {POLICIES} or NONE")
            self.policy = None if pol == "NONE" else pol
        if interval is not None:
            if interval < 0.5:
                raise ValueError("interval must be >= 0.5 seconds")
            self.interval = interval
        get_executor()                                 # fail fast (e.g. Docker not reachable) before spawning a thread
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cloudsched-automation", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def status(self) -> dict:
        return {"running": self.running, "executor": settings.executor_mode, "interval_s": self.interval,
                "policy": self.policy or "NONE", "aging_seconds": self.aging_seconds,
                "time_scale": settings.time_scale, "ticks": self.ticks, "last_tick_at": self.last_tick_at,
                "last_result": self.last_result, "last_error": self.last_error}


automation = Automation()
