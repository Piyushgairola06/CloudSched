from fastapi import APIRouter, HTTPException, Query

from app.services.automation import automation

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("")
def status():
    return automation.status()


@router.post("/start")
def start(policy: str | None = Query(None, description="FCFS | SJF | PRIORITY | PRIORITY_AGING | NONE"),
          interval: float | None = Query(None, ge=0.5)):
    try:
        automation.start(policy, interval)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return automation.status()


@router.post("/stop")
def stop():
    automation.stop()
    return automation.status()


@router.post("/tick")
def tick_once():
    """Run ONE cycle now (works whether or not the background loop is running)."""
    try:
        return {"result": automation.run_once(), "status": automation.status()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
