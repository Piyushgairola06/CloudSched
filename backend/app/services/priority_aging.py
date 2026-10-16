"""Priority aging (starvation prevention). Smaller number = higher priority.

effective_priority = max(best, base - floor(waited / interval) * step)
A job that has waited `interval` units gets `step` better. With finite jobs ahead of it, a
waiting job eventually reaches `best` and then wins ties through its earlier arrival time.
Assumption: finite job supply and non-preemptive dispatch; it is a guarantee under those rules only.
"""
from app.services.scheduling_algorithms import run_nonpreemptive


def effective_priority(base: int, waited: int, interval: int, step: int = 1, best: int = 1) -> int:
    if interval <= 0 or step <= 0:
        raise ValueError("interval and step must be > 0")
    return max(best, base - (max(waited, 0) // interval) * step)


def priority_with_aging(procs, interval: int, step: int = 1, best: int = 1):
    """Non-preemptive priority scheduling where waiting time improves a job's priority."""
    return run_nonpreemptive(
        procs,
        lambda p, t: (effective_priority(p.priority, t - p.arrival, interval, step, best), p.arrival, p.pid),
    )
