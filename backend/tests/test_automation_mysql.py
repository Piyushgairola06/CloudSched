"""Automation against a REAL MySQL: launch the containers the seed placed, then reap them when they finish.

Needs a FRESHLY seeded database (database/install.sql) because it relies on the seed's CREATED containers, and it
completes those seed jobs (run it in CI or on a throw-away database, not on a demo you want to keep):
    export MYSQL_TEST_URL=mysql+pymysql://root:PASSWORD@127.0.0.1:3306/cloudsched
    pytest tests/test_automation_mysql.py -v
"""
import os
import time

import pytest
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session

URL = os.environ.get("MYSQL_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MYSQL_TEST_URL to a freshly seeded MySQL")


def test_tick_launches_then_reaps_seeded_containers():
    from app.services.automation import tick
    from app.services.executor import SimExecutor

    ex = SimExecutor(time_scale=0.001)                 # every job lasts the 1-second minimum
    engine = create_engine(URL)
    with Session(engine) as db:
        created = [r[0] for r in db.execute(text("SELECT job_id FROM containers WHERE status = 'CREATED'"))]
        db.commit()
        assert created, "needs the fresh seed (database/05_seed_data.sql places containers)"

        first = tick(db, ex, policy=None, reap=False)
        assert sorted(x["job_id"] for x in first["launched"]) == sorted(created)
        assert db.execute(text("SELECT COUNT(*) FROM containers WHERE status = 'RUNNING'")).scalar() >= len(created)
        db.commit()

        time.sleep(2.2)
        second = tick(db, ex, policy="PRIORITY_AGING", aging_seconds=60)
        assert {f["job_id"] for f in second["finished"]} >= set(created)
        assert all(f["status"] == "COMPLETED" for f in second["finished"])
        statuses = {r[0] for r in db.execute(text("SELECT status FROM jobs WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)), {"ids": created})}
        assert statuses == {"COMPLETED"}
    engine.dispose()
