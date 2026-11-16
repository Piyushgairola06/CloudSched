"""API tests use SQLite for HTTP/model behaviour and mock the MySQL procedure boundary.
Real stored procedures, triggers, row locks and concurrency require a local MySQL 8 instance.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.session import Base, get_db
from app.main import app
from app.models import job as _job  # noqa: F401
from app.models.job import Job, JobStatus

VALID = {"name": "j1", "cpu_cores": 2, "ram_mb": 1024, "storage_mb": 100, "est_time": 10, "priority": 3}


@pytest.fixture()
def client(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    def fake_submit(db, *, user_id, name, cpu, ram_mb, storage_mb, est_time, priority=5,
                    arrival=0, deadline=None, job_type="CPU_BOUND"):
        if cpu > 8 or ram_mb > 16384 or storage_mb > 102400:
            raise ValueError("Job request exceeds total host capacity")
        job = Job(name=name, cpu_cores=cpu, ram_mb=ram_mb, storage_mb=storage_mb,
                  est_time=est_time, priority=priority, arrival_time=arrival,
                  deadline=deadline, job_type=job_type, status=JobStatus.WAITING)
        db.add(job)
        db.commit()
        return job.id

    monkeypatch.setattr("app.api.jobs.submit_job", fake_submit)
    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_and_get(client):
    r = client.post("/api/jobs", json=VALID)
    assert r.status_code == 201 and r.json()["status"] == "WAITING"
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["name"] == "j1"


@pytest.mark.parametrize("patch", [
    {"cpu_cores": 0}, {"ram_mb": -5}, {"est_time": 0}, {"priority": 11}, {"arrival_time": -1},
    {"deadline": 5},
])
def test_invalid_input_rejected(client, patch):
    assert client.post("/api/jobs", json={**VALID, **patch}).status_code == 422


def test_request_larger_than_host_rejected(client):
    assert client.post("/api/jobs", json={**VALID, "cpu_cores": 9999}).status_code == 422


def test_cancel_and_double_cancel(client):
    jid = client.post("/api/jobs", json=VALID).json()["id"]
    assert client.post(f"/api/jobs/{jid}/cancel").json()["status"] == "CANCELLED"
    assert client.post(f"/api/jobs/{jid}/cancel").status_code == 409


def test_missing_job_404(client):
    assert client.get("/api/jobs/999").status_code == 404
