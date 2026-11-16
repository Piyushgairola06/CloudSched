"""Bridge between the OS lab and the DBMS: the OS algorithms look at the LIVE MySQL queue, and OS results are stored.

recommend_policy() is pure (no DB): it replays a waiting queue through the multi-resource cluster simulator
once per dispatch policy and scores each run, the same normalise-and-weight idea as adaptive_scheduler.py.
"""
from app.services.cluster_sim import simulate

POLICIES = ("FCFS", "SJF", "PRIORITY", "PRIORITY_AGING")
# lower is better for every metric except avg_cpu_util_pct
WEIGHTS = {"avg_wait": 0.25, "max_wait": 0.25, "priority_weighted_wait": 0.25, "makespan": 0.15, "avg_cpu_util_pct": 0.10}


def queue_to_jobs(rows):
    """DB queue rows (v_waiting_queue) -> cluster_sim job dicts. Arrival is shifted so the earliest job is t=0."""
    base = min(r["arrival_time"] for r in rows)
    return [{"pid": f"J{r['job_id']:07d}", "arrival": r["arrival_time"] - base, "burst": r["est_time"],
             "priority": r["priority"], "cpu": r["cpu_cores"], "ram": r["ram_mb"], "storage": r["storage_mb"]}
            for r in rows]


def recommend_policy(rows, host_cpu, host_ram, host_storage, aging_interval=10):
    if not rows:
        return {"queue_size": 0, "recommended": None, "explanation": "The waiting queue is empty; nothing to compare."}
    jobs = queue_to_jobs(rows)
    prio = {j["pid"]: j["priority"] for j in jobs}
    raw = {}
    for pol in POLICIES:
        r = simulate(jobs, host_cpu, host_ram, host_storage, pol, aging_interval)
        w = {p: 11 - prio[p] for p in prio}
        pww = sum(j["wait"] * w[j["pid"]] for j in r["jobs"]) / sum(w.values())
        raw[pol] = {"avg_wait": r["avg_wait"], "max_wait": r["max_wait"], "priority_weighted_wait": round(pww, 3),
                    "makespan": r["makespan"], "avg_cpu_util_pct": r["avg_cpu_util_pct"]}
    scores = {}
    for pol in POLICIES:
        s = 0.0
        for m, wt in WEIGHTS.items():
            vals = [raw[p][m] for p in POLICIES]
            lo, hi = min(vals), max(vals)
            if hi != lo:
                s += wt * ((hi - raw[pol][m]) / (hi - lo) if m == "avg_cpu_util_pct" else (raw[pol][m] - lo) / (hi - lo))
        scores[pol] = round(s, 4)
    ranking = sorted(POLICIES, key=lambda p: (scores[p], p))
    best = ranking[0]
    text = (f"{best} gives the best weighted trade-off for the {len(jobs)} waiting job(s) "
            f"(score {scores[best]}, lower is better).")
    if len(ranking) > 1 and scores[ranking[1]] == scores[best]:
        text += f" It ties with {ranking[1]}; ties are broken alphabetically."
    return {"queue_size": len(jobs), "recommended": best, "ranking": ranking, "scores": scores, "raw": raw,
            "weights": WEIGHTS, "explanation": text + " Simulated on the current queue, not a guarantee."}


def db_algorithm(name: str) -> str:
    """adaptive_scheduler names ('RR(q=4)') -> the algorithm ENUM used by algorithm_results."""
    return "RR" if name.startswith("RR") else name
