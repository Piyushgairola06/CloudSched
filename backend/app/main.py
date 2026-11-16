import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text

from app.api import automation as automation_api
from app.api import bridge, dbms, jobs
from app.api import os as os_api
from app.core.config import settings
from app.database.session import engine
from app.models import job as _job  # noqa: F401  (register ORM mapping)
from app.services.automation import automation
from app.services.job_service import JobError

log = logging.getLogger("cloudsched")
STATIC = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The richer MySQL schema is installed explicitly from database/install.sql (or by docker compose).
    # Never auto-create a partial ORM schema over the production/demo DBMS schema.
    if settings.automation_enabled:
        try:
            automation.start()
        except Exception:
            log.exception("automation could not start; the API keeps running without it")
    yield
    automation.stop()


app = FastAPI(title="CloudSched", version="0.2.0", lifespan=lifespan)
app.include_router(os_api.router)          # OS lab: works without a database
app.include_router(jobs.router)            # DBMS workflow
app.include_router(dbms.router)
app.include_router(bridge.router)          # OS algorithms on live DB data
app.include_router(automation_api.router)  # background dispatch / VM placement / container execution


@app.exception_handler(JobError)
async def job_error_handler(request: Request, exc: JobError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    info = {"executor": settings.executor_mode, "automation_running": automation.running}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected", "dbms": "mysql", **info}
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable",
                                                      "detail": str(exc), **info})


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "index.html")
