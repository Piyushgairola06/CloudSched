from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import jobs
from app.database.session import Base, engine
from app.models import job as _job  # noqa: F401  (register model on Base)
from app.services.job_service import JobError


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)  # dev convenience; database/schema.sql is the reference schema
    yield


app = FastAPI(title="CloudSched", version="0.1.0", lifespan=lifespan)
app.include_router(jobs.router)


@app.exception_handler(JobError)
async def job_error_handler(request: Request, exc: JobError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {"status": "ok"}
