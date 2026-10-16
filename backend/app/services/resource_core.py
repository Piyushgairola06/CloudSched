"""In-memory resource manager (pure logic). The DB-backed version in Phase D must keep the same rules.

Invariants enforced
  I1 available >= 0 for every resource      I2 allocated <= total
  I3 one allocation per job (no duplicates)  I4 a job's resources are released exactly once
`allocated` is DERIVED from the allocation table, so counters can never drift out of sync.
"""


class ResourceError(Exception):
    pass


class InsufficientResources(ResourceError):
    pass


class DuplicateAllocation(ResourceError):
    pass


class NotAllocated(ResourceError):  # raised on double release / unknown job
    pass


class HostResources:
    KINDS = ("cpu", "ram", "storage")

    def __init__(self, cpu: int, ram: int, storage: int):
        if min(cpu, ram, storage) <= 0:
            raise ValueError("host capacity must be positive")
        self.total = {"cpu": cpu, "ram": ram, "storage": storage}
        self.allocations: dict[str, dict] = {}

    @property
    def allocated(self):
        return {k: sum(a[k] for a in self.allocations.values()) for k in self.KINDS}

    @property
    def available(self):
        al = self.allocated
        return {k: self.total[k] - al[k] for k in self.KINDS}

    def can_fit(self, cpu, ram, storage) -> bool:
        a = self.available
        return cpu <= a["cpu"] and ram <= a["ram"] and storage <= a["storage"]

    def allocate(self, job_id, cpu, ram, storage):
        if min(cpu, ram, storage) < 0 or (cpu, ram, storage) == (0, 0, 0):
            raise ValueError("invalid resource request")
        if job_id in self.allocations:
            raise DuplicateAllocation(f"{job_id} already holds an allocation")
        if not self.can_fit(cpu, ram, storage):
            raise InsufficientResources(f"{job_id} needs more than is available")
        self.allocations[job_id] = {"cpu": cpu, "ram": ram, "storage": storage}
        self.check_invariants()

    def release(self, job_id):
        if job_id not in self.allocations:
            raise NotAllocated(f"{job_id} has no allocation (double release?)")
        del self.allocations[job_id]
        self.check_invariants()

    def check_invariants(self):
        a, al = self.available, self.allocated
        assert all(a[k] >= 0 for k in self.KINDS), "available went negative"
        assert all(al[k] <= self.total[k] for k in self.KINDS), "allocated exceeds total"

    def utilization(self):
        al = self.allocated
        return {k: 100.0 * al[k] / self.total[k] for k in self.KINDS}
