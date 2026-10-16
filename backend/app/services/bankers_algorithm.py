"""Banker's Algorithm (deadlock AVOIDANCE) for a fixed set of resource types.
Limitation: models only the declared resource types and declared maximum demands; it does not
make every real-world deadlock impossible."""
from dataclasses import dataclass, field
from typing import Optional

GRANTED = "GRANTED"
DEFERRED_UNAVAILABLE = "DEFERRED_UNAVAILABLE"   # not enough free resources right now
DEFERRED_UNSAFE = "DEFERRED_UNSAFE"             # enough free, but granting leads to an unsafe state
INVALID = "INVALID"                             # exceeds declared max need (or negative)


@dataclass
class Decision:
    status: str
    reason: str
    safe_sequence: Optional[list] = field(default=None)


def need_matrix(max_demand, allocation):
    return {p: [m - a for m, a in zip(max_demand[p], allocation[p])] for p in max_demand}


def is_safe(available, max_demand, allocation):
    """Safety algorithm. Returns (is_safe, safe_sequence)."""
    need = need_matrix(max_demand, allocation)
    work, done, seq = list(available), set(), []
    progressed = True
    while progressed and len(done) < len(max_demand):
        progressed = False
        for p in max_demand:
            if p not in done and all(n <= w for n, w in zip(need[p], work)):
                work = [w + a for w, a in zip(work, allocation[p])]  # p finishes, returns its resources
                done.add(p)
                seq.append(p)
                progressed = True
    return len(done) == len(max_demand), (seq if len(done) == len(max_demand) else None)


def request_resources(pid, request, available, max_demand, allocation) -> Decision:
    """Decide a request WITHOUT mutating the inputs."""
    if pid not in max_demand:
        return Decision(INVALID, f"unknown process {pid}")
    if len(request) != len(available) or any(r < 0 for r in request):
        return Decision(INVALID, "request must be non-negative and match the resource count")
    need = need_matrix(max_demand, allocation)[pid]
    if any(r > n for r, n in zip(request, need)):
        return Decision(INVALID, "request exceeds the job's declared maximum need")
    if any(r > a for r, a in zip(request, available)):
        return Decision(DEFERRED_UNAVAILABLE, "not enough free resources right now")
    new_avail = [a - r for a, r in zip(available, request)]            # pretend to grant
    new_alloc = {p: list(v) for p, v in allocation.items()}
    new_alloc[pid] = [a + r for a, r in zip(new_alloc[pid], request)]
    safe, seq = is_safe(new_avail, max_demand, new_alloc)
    if safe:
        return Decision(GRANTED, "resulting state is safe", seq)
    return Decision(DEFERRED_UNSAFE, "granting would leave no safe completion order")
