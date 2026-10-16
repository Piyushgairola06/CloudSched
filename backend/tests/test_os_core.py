"""Hand-computed expectations. Textbook workload: P1(arr0,b5) P2(1,3) P3(2,8) P4(3,6)."""
import pytest

from app.services import bankers_algorithm as bk
from app.services.priority_aging import effective_priority, priority_with_aging
from app.services.scheduling_algorithms import Proc, fcfs, priority_np, round_robin, sjf

W = [Proc("P1", 0, 5), Proc("P2", 1, 3), Proc("P3", 2, 8), Proc("P4", 3, 6)]


def order(r):
    return [g["pid"] for g in r["gantt"]]


def test_fcfs():
    r = fcfs(W)
    assert order(r) == ["P1", "P2", "P3", "P4"]
    assert r["avg_waiting"] == 5.75 and r["avg_turnaround"] == 11.25 and r["avg_response"] == 5.75
    assert r["per_process"]["P4"]["completion"] == 22


def test_sjf_non_preemptive():
    r = sjf(W)
    assert order(r) == ["P1", "P2", "P4", "P3"]
    assert r["avg_waiting"] == 5.25 and r["avg_turnaround"] == 10.75


def test_round_robin_q2():
    r = round_robin(W, 2)
    c = {k: v["completion"] for k, v in r["per_process"].items()}
    assert c == {"P1": 14, "P2": 11, "P3": 22, "P4": 20}
    assert r["avg_waiting"] == 9.75 and r["avg_turnaround"] == 15.25 and r["avg_response"] == 2.0


def test_priority_lower_number_wins():
    r = priority_np([Proc("P1", 0, 3, 3), Proc("P2", 1, 2, 1), Proc("P3", 2, 1, 2)])
    assert order(r) == ["P1", "P2", "P3"]


def test_priority_tie_break_by_arrival_then_pid():
    r = priority_np([Proc("A", 0, 2, 5), Proc("C", 1, 1, 2), Proc("B", 1, 1, 2)])
    assert order(r) == ["A", "B", "C"]


def test_idle_gap_and_utilization():
    r = fcfs([Proc("P1", 0, 2), Proc("P2", 5, 2)])
    assert [(g["pid"], g["start"], g["end"]) for g in r["gantt"]] == [("P1", 0, 2), ("IDLE", 2, 5), ("P2", 5, 7)]
    assert r["cpu_utilization"] == pytest.approx(4 / 7)
    assert round_robin([Proc("P1", 0, 2), Proc("P2", 5, 2)], 3)["cpu_utilization"] == pytest.approx(4 / 7)


def test_leading_idle():
    assert fcfs([Proc("P1", 5, 2)])["gantt"][0] == {"pid": "IDLE", "start": 0, "end": 5}


def test_deadline_misses_counted():
    r = fcfs([Proc("A", 0, 5, deadline=4), Proc("B", 0, 5, deadline=10)])
    assert r["deadline_misses"] == 1


def test_invalid_inputs():
    for bad in ([], [Proc("A", 0, 0)], [Proc("A", 0, 1), Proc("A", 1, 1)]):
        with pytest.raises(ValueError):
            fcfs(bad)
    with pytest.raises(ValueError):
        round_robin(W, 0)


# ---- aging: one priority-10 job vs a stream of priority-1 jobs (each burst 5, arriving every 5) ----
def _stream():
    return [Proc("L", 0, 1, 10)] + [Proc(f"H{i}", 5 * i, 5, 1) for i in range(100)]


def test_aging_formula():
    assert effective_priority(10, 0, 10) == 10
    assert effective_priority(10, 25, 10) == 8
    assert effective_priority(3, 1000, 10) == 1  # never better than best


def test_starvation_without_aging_and_rescue_with_aging():
    no_aging = priority_np(_stream())
    assert no_aging["per_process"]["L"]["completion"] == 501          # runs dead last
    aged = priority_with_aging(_stream(), interval=10)
    assert aged["per_process"]["L"]["completion"] == 91              # starts at t=90
    assert aged["per_process"]["L"]["waiting"] == 90


# ---- Banker's (Silberschatz example): total A10 B5 C7 ----
MAX = {"P0": [7, 5, 3], "P1": [3, 2, 2], "P2": [9, 0, 2], "P3": [2, 2, 2], "P4": [4, 3, 3]}
ALLOC = {"P0": [0, 1, 0], "P1": [2, 0, 0], "P2": [3, 0, 2], "P3": [2, 1, 1], "P4": [0, 0, 2]}
AVAIL = [3, 3, 2]


def test_initial_state_safe_and_sequence_valid():
    ok, seq = bk.is_safe(AVAIL, MAX, ALLOC)
    assert ok and sorted(seq) == sorted(MAX)
    need, work = bk.need_matrix(MAX, ALLOC), list(AVAIL)
    for p in seq:                       # replay the sequence to prove it is truly safe
        assert all(n <= w for n, w in zip(need[p], work))
        work = [w + a for w, a in zip(work, ALLOC[p])]


def test_safe_request_granted():
    d = bk.request_resources("P1", [1, 0, 2], AVAIL, MAX, ALLOC)
    assert d.status == bk.GRANTED and d.safe_sequence


def test_unsafe_request_deferred():
    assert bk.request_resources("P4", [3, 3, 0], AVAIL, MAX, ALLOC).status == bk.DEFERRED_UNSAFE
    # Chained scenario: after P1's (1,0,2) is granted, P4's (3,3,0) is simply unavailable...
    a2 = [2, 3, 0]
    al2 = {**ALLOC, "P1": [3, 0, 2]}
    assert bk.request_resources("P4", [3, 3, 0], a2, MAX, al2).status == bk.DEFERRED_UNAVAILABLE
    # ...and P0's (0,2,0) leaves available [2,1,0], from which no job can finish -> unsafe.
    assert bk.request_resources("P0", [0, 2, 0], a2, MAX, al2).status == bk.DEFERRED_UNSAFE


def test_p0_request_on_initial_state_is_safe():
    # Hand check: avail becomes [3,1,2]; order P1,P3,P0,P2,P4 completes.
    assert bk.request_resources("P0", [0, 2, 0], AVAIL, MAX, ALLOC).status == bk.GRANTED


def test_unavailable_vs_invalid():
    assert bk.request_resources("P0", [5, 0, 0], AVAIL, MAX, ALLOC).status == bk.DEFERRED_UNAVAILABLE
    assert bk.request_resources("P1", [4, 0, 0], AVAIL, MAX, ALLOC).status == bk.INVALID  # need is [1,2,2]
    assert bk.request_resources("P1", [-1, 0, 0], AVAIL, MAX, ALLOC).status == bk.INVALID


def test_request_does_not_mutate_state():
    before = ([*AVAIL], {k: [*v] for k, v in ALLOC.items()})
    bk.request_resources("P1", [1, 0, 2], AVAIL, MAX, ALLOC)
    assert before == ([*AVAIL], {k: [*v] for k, v in ALLOC.items()})
