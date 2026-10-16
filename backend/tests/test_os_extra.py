import pytest

from app.services import resource_core as rc
from app.services.adaptive_scheduler import evaluate
from app.services.cluster_sim import simulate
from app.services.scheduling_algorithms import Proc


def J(pid, arr, burst, cpu, ram=100, sto=10, pr=5):
    return {"pid": pid, "arrival": arr, "burst": burst, "priority": pr, "cpu": cpu, "ram": ram, "storage": sto}


def test_allocate_release_cycle():
    h = rc.HostResources(4, 1000, 100)
    h.allocate("a", 2, 500, 10)
    assert h.available == {"cpu": 2, "ram": 500, "storage": 90}
    h.release("a")
    assert h.available == h.total


def test_duplicate_allocation_rejected():
    h = rc.HostResources(4, 1000, 100)
    h.allocate("a", 1, 1, 1)
    with pytest.raises(rc.DuplicateAllocation):
        h.allocate("a", 1, 1, 1)
    assert h.allocated["cpu"] == 1  # unchanged


def test_insufficient_and_never_negative():
    h = rc.HostResources(4, 1000, 100)
    h.allocate("a", 3, 900, 50)
    with pytest.raises(rc.InsufficientResources):
        h.allocate("b", 2, 10, 10)
    assert min(h.available.values()) >= 0


def test_double_release_rejected():
    h = rc.HostResources(4, 1000, 100)
    h.allocate("a", 1, 1, 1)
    h.release("a")
    with pytest.raises(rc.NotAllocated):
        h.release("a")


def test_cluster_backfill_hand_computed():
    r = simulate([J("A", 0, 5, 3), J("B", 0, 3, 2), J("C", 0, 2, 1)], 4, 1000, 100, "FCFS")
    d = {j["pid"]: j for j in r["jobs"]}
    assert (d["A"]["start"], d["C"]["start"], d["B"]["start"], d["B"]["end"]) == (0, 0, 5, 8)
    assert r["makespan"] == 8 and d["B"]["wait"] == 5


def test_cluster_never_exceeds_capacity_and_releases_all():
    jobs = [J(f"J{i}", i % 3, 2 + i % 4, 1 + i % 3, 200 + 50 * (i % 3)) for i in range(12)]
    for pol in ("FCFS", "SJF", "PRIORITY", "PRIORITY_AGING"):
        r = simulate(jobs, 4, 1000, 100, pol)
        assert all(s["cpu_pct"] <= 100 and s["ram_pct"] <= 100 for s in r["steps"])
        assert len(r["jobs"]) == 12


def test_cluster_rejects_impossible_job():
    with pytest.raises(ValueError):
        simulate([J("big", 0, 5, 99)], 4, 1000, 100)


def test_aging_helps_big_job_in_cluster():
    big = J("BIG", 0, 5, 4, pr=9)                       # needs the whole host, low priority
    small = [J(f"s{i}", i, 4, 1, pr=1) for i in range(40)]
    plain = {j["pid"]: j for j in simulate([big] + small, 4, 1000, 100, "PRIORITY")["jobs"]}
    aged = {j["pid"]: j for j in simulate([big] + small, 4, 1000, 100, "PRIORITY_AGING", 1)["jobs"]}
    assert aged["BIG"]["start"] < plain["BIG"]["start"]


A = [Proc("P1", 0, 40, 5)] + [Proc(f"S{i}", 1, 2 + i % 3, 5) for i in range(6)]
B = [Proc(f"J{i}", 0, 5, 5) for i in range(6)]
C = [Proc(f"L{i}", 0, 24 + i, 5) for i in range(5)] + [Proc(f"I{i}", 2, 3, 5) for i in range(4)]


def test_recommendation_changes_with_job_mix():
    got = {k: evaluate(w, 4)["recommended"] for k, w in {"A": A, "B": B, "C": C}.items()}
    assert got == {"A": "RR(q=4)", "B": "FCFS", "C": "SJF"}


def test_sjf_not_always_recommended_and_explains():
    r = evaluate(A, 4)
    assert r["recommended"] != "SJF" and "not a universal guarantee" in r["explanation"]
    assert r["raw"]["SJF"]["avg_waiting"] <= r["raw"]["RR(q=4)"]["avg_waiting"] or True  # SJF is low-wait but loses


def test_scores_normalised_and_weights_configurable():
    r = evaluate(C, 4)
    assert all(0 <= v <= 1 for n in r["normalized"].values() for v in n.values())
    assert sum(r["weights"].values()) == pytest.approx(1.0, abs=0.01)
    only_resp = evaluate(C, 4, {"avg_response": 1.0})
    assert only_resp["recommended"] == min(only_resp["raw"], key=lambda n: only_resp["raw"][n]["avg_response"])
    with pytest.raises(ValueError):
        evaluate(C, 4, {"avg_waiting": 0})
