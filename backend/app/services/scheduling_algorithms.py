"""Pure CPU-scheduling logic. No database, no web framework: unit-testable on its own.

Conventions
-----------
* Time is an integer number of simulation units. Simulation starts at t = 0.
* Priority: SMALLER number = HIGHER priority (1 is best).
* Tie-breaking (documented, deterministic):
    FCFS      -> (arrival, pid)
    SJF       -> (burst, arrival, pid)
    Priority  -> (priority, arrival, pid)
* Round Robin: a process arriving at the same instant a quantum expires is queued
  BEFORE the pre-empted process (standard textbook rule).

Formulas (per process)
    turnaround = completion - arrival
    waiting    = turnaround - burst
    response   = first_start - arrival
Aggregates
    throughput      = n / makespan            (makespan = last completion time)
    cpu_utilization = busy_time / makespan    (idle gaps lower it)
"""
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

IDLE = "IDLE"


@dataclass(frozen=True)
class Proc:
    pid: str
    arrival: int
    burst: int
    priority: int = 5
    deadline: Optional[int] = None  # absolute time


def _validate(procs, quantum=None):
    if not procs:
        raise ValueError("no processes given")
    if len({p.pid for p in procs}) != len(procs):
        raise ValueError("duplicate pid")
    for p in procs:
        if p.burst <= 0 or p.arrival < 0:
            raise ValueError(f"invalid burst/arrival for {p.pid}")
    if quantum is not None and quantum <= 0:
        raise ValueError("quantum must be > 0")


def _add_seg(gantt, pid, start, end):
    if gantt and gantt[-1][0] == pid and gantt[-1][2] == start:
        gantt[-1] = (pid, gantt[-1][1], end)  # merge contiguous slices of same pid
    else:
        gantt.append((pid, start, end))


def _finish(procs, completion, first_start, gantt):
    per = {}
    for p in procs:
        tat = completion[p.pid] - p.arrival
        per[p.pid] = {
            "completion": completion[p.pid], "turnaround": tat,
            "waiting": tat - p.burst, "response": first_start[p.pid] - p.arrival,
            "deadline_missed": p.deadline is not None and completion[p.pid] > p.deadline,
        }
    n = len(procs)
    makespan = max(completion.values())
    busy = sum(e - s for pid, s, e in gantt if pid != IDLE)
    return {
        "per_process": per,
        "gantt": [{"pid": a, "start": b, "end": c} for a, b, c in gantt],
        "avg_waiting": sum(v["waiting"] for v in per.values()) / n,
        "avg_turnaround": sum(v["turnaround"] for v in per.values()) / n,
        "avg_response": sum(v["response"] for v in per.values()) / n,
        "max_waiting": max(v["waiting"] for v in per.values()),
        "throughput": n / makespan,
        "cpu_utilization": busy / makespan,
        "makespan": makespan,
        "deadline_misses": sum(v["deadline_missed"] for v in per.values()),
    }


def run_nonpreemptive(procs, keyfn: Callable[[Proc, int], tuple]):
    """Generic non-preemptive scheduler: at each decision pick min keyfn(p, now) among arrived jobs."""
    _validate(procs)
    pending, t = sorted(procs, key=lambda p: (p.arrival, p.pid)), 0
    completion, first_start, gantt = {}, {}, []
    while pending:
        ready = [p for p in pending if p.arrival <= t]
        if not ready:                                   # CPU idle until next arrival
            nxt = min(p.arrival for p in pending)
            _add_seg(gantt, IDLE, t, nxt)
            t = nxt
            continue
        p = min(ready, key=lambda q: keyfn(q, t))
        first_start[p.pid] = t
        _add_seg(gantt, p.pid, t, t + p.burst)
        t += p.burst
        completion[p.pid] = t
        pending.remove(p)
    return _finish(procs, completion, first_start, gantt)


def fcfs(procs):
    return run_nonpreemptive(procs, lambda p, t: (p.arrival, p.pid))


def sjf(procs):
    return run_nonpreemptive(procs, lambda p, t: (p.burst, p.arrival, p.pid))


def priority_np(procs):
    return run_nonpreemptive(procs, lambda p, t: (p.priority, p.arrival, p.pid))


def round_robin(procs, quantum):
    _validate(procs, quantum)
    ordered = sorted(procs, key=lambda p: (p.arrival, p.pid))
    n, i, t = len(ordered), 0, 0
    q, rem = deque(), {p.pid: p.burst for p in ordered}
    completion, first_start, gantt = {}, {}, []

    def admit(now):
        nonlocal i
        while i < n and ordered[i].arrival <= now:
            q.append(ordered[i])
            i += 1

    while i < n or q:
        if not q:
            _add_seg(gantt, IDLE, t, ordered[i].arrival)
            t = ordered[i].arrival
            admit(t)
        p = q.popleft()
        run = min(quantum, rem[p.pid])
        first_start.setdefault(p.pid, t)
        _add_seg(gantt, p.pid, t, t + run)
        t += run
        rem[p.pid] -= run
        admit(t)                      # new arrivals go in front of the pre-empted job
        if rem[p.pid] > 0:
            q.append(p)
        else:
            completion[p.pid] = t
    return _finish(procs, completion, first_start, gantt)


ALGORITHMS = {"FCFS": fcfs, "SJF": sjf, "PRIORITY": priority_np}
