"""Tests for the merged layer that need neither MySQL nor Docker: OS lab API logic, workloads, executors, bridge."""
import subprocess
import sys

import pytest

from app.services import os_lab
from app.services.bridge import POLICIES, db_algorithm, queue_to_jobs, recommend_policy
from app.services.executor import DockerExecutor, ExecJob, SimExecutor, duration_seconds, workload_command


# ------------------------------------------------------------------ OS lab
def test_lab_textbook_fcfs_matches_docs():
    rows = [dict(zip(("pid", "arrival", "burst", "priority"), l.split(","))) for l in
            os_lab.PRESETS["workloads"]["Textbook (P1..P4)"].splitlines()]
    r = os_lab.schedule({"jobs": rows, "algorithm": "FCFS"})
    assert r["avg_waiting"] == 5.75


def test_lab_banker_preset_decisions():
    b = os_lab.PRESETS["banker"]
    base = {"max": b["max"], "allocation": b["allocation"], "available": b["available"]}
    assert os_lab.banker(base)["safe"] is True
    assert os_lab.banker({**base, "pid": "P1", "request": [1, 0, 2]})["decision"]["status"] == "GRANTED"
    assert os_lab.banker({**base, "pid": "P4", "request": [3, 3, 0]})["decision"]["status"] == "DEFERRED_UNSAFE"
    assert os_lab.banker({**base, "pid": "P0", "request": [5, 0, 0]})["decision"]["status"] == "DEFERRED_UNAVAILABLE"
    assert os_lab.banker({**base, "pid": "P1", "request": [4, 0, 0]})["decision"]["status"] == "INVALID"


def test_lab_aging_demo_matches_docs():
    r = os_lab.aging_demo({"interval": 10})
    assert r["without_aging"]["completion"] == 501 and r["with_aging"]["completion"] == 91


def test_lab_cluster_preset_runs():
    jobs = [dict(zip(("pid", "arrival", "burst", "priority", "cpu", "ram", "storage"), l.split(",")))
            for l in os_lab.PRESETS["cluster"].splitlines()]
    r = os_lab.cluster({"jobs": jobs, "host": {"cpu": 8, "ram": 16384, "storage": 102400}, "policy": "FCFS"})
    assert r["makespan"] > 0 and len(r["jobs"]) == 5


def test_db_algorithm_names():
    assert db_algorithm("RR(q=4)") == "RR" and db_algorithm("SJF") == "SJF"


# ------------------------------------------------------------------ workloads actually run
@pytest.mark.parametrize("jt", ["CPU_BOUND", "MEMORY_BOUND", "IO_BOUND", "MIXED"])
def test_each_workload_runs_and_exits_zero(jt):
    cmd = workload_command(ExecJob(1, jt, cpu=2, ram_mb=64, est_time=1), time_scale=1)
    assert not any(ph in cmd[2] for ph in ("__SECS__", "__NPROC__", "__MB__"))   # all placeholders substituted
    assert subprocess.run([sys.executable, "-c", cmd[2]], timeout=60).returncode == 0


def test_unknown_job_type_rejected():
    with pytest.raises(ValueError):
        workload_command(ExecJob(1, "ROOTKIT", 1, 64, 1))


# ------------------------------------------------------------------ executors
def test_sim_executor_finishes_after_scaled_duration():
    ex = SimExecutor(time_scale=0.5)
    assert duration_seconds(10, 0.5) == 5.0
    assert ex.poll(None, ExecJob(1, "CPU_BOUND", 1, 64, 10, elapsed_s=4.9)) is None
    assert ex.poll(None, ExecJob(1, "CPU_BOUND", 1, 64, 10, elapsed_s=5.0)) == 0
    assert ex.start(ExecJob(1, "CPU_BOUND", 1, 64, 10)) is None


class _NF(Exception):
    pass


class _Cont:
    def __init__(self, status, code=0):
        self.id, self.status, self.attrs, self.removed = "c" * 64, status, {"State": {"ExitCode": code}}, False

    def reload(self):
        pass

    def remove(self, force=False):
        self.removed = True


class _FakeClient:
    def __init__(self):
        self.store, self.run_kwargs = {}, None
        self.containers = self

    def run(self, image, **kw):
        self.run_kwargs = {"image": image, **kw}
        self.store[kw["name"]] = c = _Cont("running")
        return c

    def get(self, ref):
        for name, c in self.store.items():
            if ref in (name, c.id):
                return c
        raise _NF()


def test_docker_executor_limits_and_lifecycle():
    cli = _FakeClient()
    ex = DockerExecutor(client=cli, image="img:1", not_found=_NF)
    job = ExecJob(7, "CPU_BOUND", cpu=3, ram_mb=512, est_time=2)
    ref = ex.start(job)
    kw = cli.run_kwargs
    assert kw["nano_cpus"] == 3_000_000_000 and kw["mem_limit"] == "512m" and kw["memswap_limit"] == "512m"
    assert kw["network_mode"] == "none" and kw["cap_drop"] == ["ALL"] and kw["read_only"] is True
    assert ex.poll(ref, job) is None                         # still running
    cli.store["cloudsched-job-7"].status = "exited"
    assert ex.poll(ref, job) == 0
    cli.store["cloudsched-job-7"].attrs["State"]["ExitCode"] = 137
    assert ex.poll(ref, job) == 137                          # OOM kill -> the caller turns this into FAILED
    ex.cleanup(ref)
    assert cli.store["cloudsched-job-7"].removed
    assert ex.poll("gone", job) == 1                         # vanished container -> failure, not a crash


# ------------------------------------------------------------------ bridge: OS algorithms on a DB-shaped queue
def _q(i, prio, arr, est, cpu, ram):
    return {"job_id": i, "priority": prio, "arrival_time": arr, "est_time": est,
            "cpu_cores": cpu, "ram_mb": ram, "storage_mb": 100}


def test_queue_to_jobs_shifts_arrivals_and_pads_ids():
    jobs = queue_to_jobs([_q(5, 1, 10, 3, 1, 64), _q(12, 2, 12, 3, 1, 64)])
    assert [j["arrival"] for j in jobs] == [0, 2] and jobs[0]["pid"] < jobs[1]["pid"]


def test_recommend_policy_on_mixed_queue():
    rows = [_q(1, 5, 0, 40, 6, 8192), _q(2, 5, 1, 2, 1, 512), _q(3, 5, 1, 3, 1, 512), _q(4, 1, 2, 4, 2, 1024)]
    r = recommend_policy(rows, 8, 16384, 102400)
    assert r["recommended"] in POLICIES and set(r["scores"]) == set(POLICIES)
    assert r["scores"][r["recommended"]] == min(r["scores"].values())
    assert recommend_policy(rows, 8, 16384, 102400) == r     # deterministic


def test_recommend_policy_empty_queue():
    assert recommend_policy([], 8, 16384, 102400)["recommended"] is None


def test_recommend_policy_rejects_job_that_can_never_fit():
    with pytest.raises(ValueError):
        recommend_policy([_q(1, 5, 0, 5, 99, 64)], 8, 16384, 102400)
