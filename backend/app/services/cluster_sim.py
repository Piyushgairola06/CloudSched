"""Multi-job execution simulator: jobs run CONCURRENTLY while CPU/RAM/storage last; others WAIT.
At every decision point the policy picks the best-ranked ready job THAT FITS (backfilling).
Policies: FCFS, SJF, PRIORITY (all backfill), PRIORITY_AGING (no backfill around the top-ranked job).
Known limitation of backfilling: a big job can be bypassed forever by a stream of small ones.
PRIORITY_AGING fixes this: once aging makes a waiting job the top-ranked one, it RESERVES resources
(nothing else is started until it fits), so it cannot starve. This is a simulation, not a real OS."""
from app.services.priority_aging import effective_priority
from app.services.resource_core import HostResources


def _key(policy, aging_interval):
    if policy == "FCFS":
        return lambda j, t: (j["arrival"], j["pid"])
    if policy == "SJF":
        return lambda j, t: (j["burst"], j["arrival"], j["pid"])
    if policy == "PRIORITY":
        return lambda j, t: (j["priority"], j["arrival"], j["pid"])
    if policy == "PRIORITY_AGING":
        iv = aging_interval or 10
        return lambda j, t: (effective_priority(j["priority"], t - j["arrival"], iv), j["arrival"], j["pid"])
    raise ValueError(f"unknown policy {policy}")


def simulate(jobs, host_cpu, host_ram, host_storage, policy="FCFS", aging_interval=None):
    host = HostResources(host_cpu, host_ram, host_storage)
    pids = [j["pid"] for j in jobs]
    if not jobs or len(set(pids)) != len(pids):
        raise ValueError("need jobs with unique pids")
    for j in jobs:
        if j["burst"] <= 0 or j["arrival"] < 0:
            raise ValueError(f"{j['pid']}: invalid burst/arrival")
        if (j["cpu"] > host_cpu or j["ram"] > host_ram or j["storage"] > host_storage):
            raise ValueError(f"{j['pid']}: request can never fit on this host (rejected)")
    key = _key(policy, aging_interval)
    pending, running, done, steps, t = list(jobs), [], {}, [], 0
    while pending or running:
        while True:  # start everything that fits right now
            ready = [j for j in pending if j["arrival"] <= t]
            if policy != "PRIORITY_AGING":          # backfilling: only consider jobs that fit
                ready = [j for j in ready if host.can_fit(j["cpu"], j["ram"], j["storage"])]
            if not ready:
                break
            j = min(ready, key=lambda x: key(x, t))
            if not host.can_fit(j["cpu"], j["ram"], j["storage"]):
                break                               # reservation: wait for releases, don't bypass
            host.allocate(j["pid"], j["cpu"], j["ram"], j["storage"])
            pending.remove(j)
            running.append((t + j["burst"], j))
            done[j["pid"]] = {"pid": j["pid"], "arrival": j["arrival"], "start": t, "end": t + j["burst"],
                              "wait": t - j["arrival"], "cpu": j["cpu"], "ram": j["ram"]}
        u = host.utilization()
        steps.append({"t": t, "cpu_pct": round(u["cpu"], 1), "ram_pct": round(u["ram"], 1),
                      "storage_pct": round(u["storage"], 1), "running": sorted(x[1]["pid"] for x in running),
                      "waiting": sorted(x["pid"] for x in pending if x["arrival"] <= t)})
        nxt = [e for e, _ in running] + [j["arrival"] for j in pending if j["arrival"] > t]
        t = min(nxt)
        for e, j in [r for r in running if r[0] == t]:
            host.release(j["pid"])            # released exactly once, here
            running.remove((e, j))
    makespan = max(d["end"] for d in done.values())
    cpu_area = sum((d["end"] - d["start"]) * d["cpu"] for d in done.values())
    ram_area = sum((d["end"] - d["start"]) * d["ram"] for d in done.values())
    waits = [d["wait"] for d in done.values()]
    assert host.allocated == {"cpu": 0, "ram": 0, "storage": 0}, "leak: resources not fully released"
    return {"jobs": list(done.values()), "steps": steps, "makespan": makespan,
            "avg_wait": sum(waits) / len(waits), "max_wait": max(waits),
            "avg_cpu_util_pct": round(100 * cpu_area / (makespan * host_cpu), 1),
            "avg_ram_util_pct": round(100 * ram_area / (makespan * host_ram), 1),
            "peak_cpu_pct": max(s["cpu_pct"] for s in steps), "policy": policy}
