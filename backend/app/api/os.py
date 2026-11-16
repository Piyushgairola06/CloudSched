"""OS lab endpoints. They need NO database, so the scheduling lab works even when MySQL is down."""
from fastapi import APIRouter, Body, HTTPException

from app.services import os_lab

router = APIRouter(prefix="/api/os", tags=["OS lab"])


@router.get("/presets")
def presets():
    return os_lab.PRESETS


@router.post("/{operation}")
def run(operation: str, payload: dict = Body(default_factory=dict)):
    """operation = schedule | recommend | cluster | banker | aging (JSON shapes: see app/services/os_lab.py)."""
    fn = os_lab.OPERATIONS.get(operation)
    if fn is None:
        raise HTTPException(status_code=404, detail=f"unknown operation '{operation}'")
    try:
        return fn(payload)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"missing or unknown field: {exc}") from exc
    except (ValueError, TypeError, AssertionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
