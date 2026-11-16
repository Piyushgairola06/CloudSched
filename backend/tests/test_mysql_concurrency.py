"""Concurrency test for the DBMS layer (the 'Transaction and concurrency test' item of the project report).

Needs a REAL MySQL 8 with database/01..04 loaded (SQLite has no row locks or stored procedures), so it is skipped
unless you point it at one:
    set MYSQL_TEST_URL=mysql+pymysql://cloudsched_user:PASSWORD@127.0.0.1:3306/cloudsched     (Windows)
    export MYSQL_TEST_URL=...                                                                  (Linux/macOS)
    pytest tests/test_mysql_concurrency.py -v
It creates its own jobs (names start with 'ctest-') and removes them afterwards. Run it on an otherwise idle database.
"""
import os
import threading

import pytest
from sqlalchemy import create_engine, text

URL = os.environ.get("MYSQL_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MYSQL_TEST_URL to a MySQL with the CloudSched schema loaded")


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(URL, pool_size=10)
    yield eng
    with eng.begin() as c:                      # cleanup: RUNNING jobs must be finished before they can be deleted
        for (jid,) in c.execute(text("SELECT id FROM jobs WHERE name LIKE 'ctest-%' AND status = 'RUNNING'")).all():
            c.execute(text("CALL sp_release_job(:j, 'COMPLETED')"), {"j": jid})
        c.execute(text("DELETE FROM jobs WHERE name LIKE 'ctest-%'"))
    eng.dispose()


def _submit(conn, name, cpu):
    conn.execute(text("CALL sp_submit_job(NULL, :n, :c, 64, 0, 5, 5, 0, NULL, 'CPU_BOUND', @id)"), {"n": name, "c": cpu})
    return conn.execute(text("SELECT @id")).scalar_one()


def _allocate(engine, job_id, out, barrier):
    with engine.connect() as conn:
        barrier.wait()                          # release all threads at the same moment
        conn.execute(text("CALL sp_allocate_job(:j, @r)"), {"j": job_id})
        out.append(conn.execute(text("SELECT @r")).scalar_one())


def test_two_jobs_cannot_both_claim_the_last_cores(engine):
    with engine.connect() as conn:
        free = conn.execute(text("SELECT available FROM v_resource_usage WHERE resource_type = 'CPU'")).scalar_one()
        if free < 1:
            pytest.skip("no free CPU on the host; release some jobs first")
        ids = [_submit(conn, f"ctest-race-{i}", int(free)) for i in range(4)]   # 4 jobs, each wants ALL free cores
        conn.commit()

    results, barrier = [], threading.Barrier(len(ids))
    threads = [threading.Thread(target=_allocate, args=(engine, j, results, barrier)) for j in ids]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert results.count("GRANTED") == 1, results                    # exactly one winner
    assert results.count("DEFERRED_UNAVAILABLE") == len(ids) - 1, results
    with engine.connect() as conn:                                    # invariants I1/I2 hold
        rows = conn.execute(text("SELECT allocated, total_capacity, available FROM v_resource_usage")).all()
    assert all(a <= t and av >= 0 for a, t, av in rows), rows
