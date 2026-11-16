
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
