"""Phase A tests. Use in-memory SQLite so they need no MySQL server.
NOTE: MySQL-specific behaviour (row locks, concurrency) is NOT covered here; that is Phase D."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.session import Base, get_db
from app.main import app
from app.models import job as _job  # noqa: F401

VALID = {"name": "j1", "cpu_cores": 2, "ram_mb": 1024, "storage_mb": 100, "est_time": 10, "priority": 3}


@pytest.fixture()
def client():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_and_get(client):
    r = client.post("/api/jobs", json=VALID)
    assert r.status_code == 201 and r.json()["status"] == "SUBMITTED"
    assert client.get(f"/api/jobs/{r.json()['id']}").json()["name"] == "j1"


@pytest.mark.parametrize("patch", [
    {"cpu_cores": 0}, {"ram_mb": -5}, {"est_time": 0}, {"priority": 11}, {"arrival_time": -1},
    {"deadline": 5},  # < arrival + est_time -> impossible
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
