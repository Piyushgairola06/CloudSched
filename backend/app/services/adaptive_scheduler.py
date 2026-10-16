"""Rule-based adaptive scheduler: run every candidate on the SAME workload, normalise each metric to
0..1 (0 = best candidate, 1 = worst), combine with configurable weights, pick the lowest score.

Normalisation (per metric, across candidates): badness = (v - min) / (max - min); if max == min -> 0.
For throughput (higher is better) badness = (max - v) / (max - min).
Fairness / starvation: `max_waiting` (the worst-treated job) is a scored metric, so an algorithm that
starves one job is penalised even if its AVERAGE waiting is the best (this is why SJF is not auto-picked).
Priority: `priority_weighted_waiting` weights each job's wait by (11 - priority).
Deadlines: `deadline_misses` is scored. Rule: effective weight = base + 0.30 * (fraction of jobs that have a
deadline), so deadlines matter more when more of the workload is deadline-bound; with no deadlines it is 0.
The result is a recommendation on SIMULATED metrics for this workload, not a universal optimum."""
import statistics

from app.services.scheduling_algorithms import fcfs, priority_np, round_robin, sjf

DEFAULT_WEIGHTS = {"avg_waiting": 0.20, "avg_turnaround": 0.15, "avg_response": 0.20, "max_waiting": 0.20,
                   "priority_weighted_waiting": 0.10, "deadline_misses": 0.10, "throughput": 0.05}
LOWER_BETTER = [m for m in DEFAULT_WEIGHTS if m != "throughput"]
LABEL = {"avg_waiting": "average waiting time", "avg_turnaround": "average turnaround time",
         "avg_response": "response time", "max_waiting": "fairness (worst-case wait)",
         "priority_weighted_waiting": "waiting of high-priority jobs", "deadline_misses": "deadline misses",
         "throughput": "throughput"}


def analyze_workload(procs):
    b = [p.burst for p in procs]
    mean = statistics.mean(b)
    cv = statistics.pstdev(b) / mean
    return {"job_count": len(procs), "avg_burst": round(mean, 2), "burst_cv": round(cv, 2),
            "distinct_priorities": len({p.priority for p in procs}),
            "jobs_with_deadlines": sum(p.deadline is not None for p in procs),
            "arrival_span": max(p.arrival for p in procs) - min(p.arrival for p in procs)}


def _pww(procs, res):
    w = {p.pid: 11 - p.priority for p in procs}
    return sum(res["per_process"][p.pid]["waiting"] * w[p.pid] for p in procs) / sum(w.values())


def evaluate(procs, quantum=4, weights=None):
    weights = dict(weights or DEFAULT_WEIGHTS)
    stats = analyze_workload(procs)
    frac = stats["jobs_with_deadlines"] / stats["job_count"]
    weights["deadline_misses"] = 0.0 if frac == 0 else weights["deadline_misses"] + 0.30 * frac
    total_w = sum(weights.values())
    if total_w <= 0 or any(v < 0 for v in weights.values()):
        raise ValueError("weights must be non-negative and not all zero")
    weights = {k: v / total_w for k, v in weights.items()}
    runs = {"FCFS": fcfs(procs), "SJF": sjf(procs), "PRIORITY": priority_np(procs),
            f"RR(q={quantum})": round_robin(procs, quantum)}
    raw = {}
    for name, r in runs.items():
        raw[name] = {**{m: r[m] for m in ("avg_waiting", "avg_turnaround", "avg_response", "max_waiting",
                                          "deadline_misses", "throughput")},
                     "priority_weighted_waiting": _pww(procs, r), "cpu_utilization": r["cpu_utilization"]}
    norm = {n: {} for n in raw}
    for m in weights:
        vals = [raw[n][m] for n in raw]
        lo, hi = min(vals), max(vals)
        for n in raw:
            if hi == lo:
                norm[n][m] = 0.0
            else:
                norm[n][m] = (hi - raw[n][m]) / (hi - lo) if m == "throughput" else (raw[n][m] - lo) / (hi - lo)
    scores = {n: round(sum(weights[m] * norm[n][m] for m in weights), 4) for n in raw}
    ranking = sorted(scores, key=lambda n: (scores[n], n))
    best = ranking[0]
    leads = [LABEL[m] for m in weights if weights[m] > 0 and norm[best][m] == 0.0 and
             any(norm[o][m] > 0 for o in raw)]
    text = f"{best} was recommended (score {scores[best]}, lower is better; runner-up {ranking[1]} at {scores[ranking[1]]})."
    text += f" It is best on: {', '.join(leads)}." if leads else " It has no single winning metric but the best weighted trade-off."
    lowest_wait = min(raw, key=lambda n: raw[n]["avg_waiting"])
    if lowest_wait != best:
        text += (f" {lowest_wait} has the lowest average waiting time, but it was not chosen because of "
                 f"worse results on other weighted criteria (e.g. fairness/response/priority).")
    text += " This is based on simulated metrics for this workload, not a universal guarantee of optimality."
    return {"workload": stats, "weights": {k: round(v, 3) for k, v in weights.items()}, "raw": raw,
            "normalized": norm, "scores": scores, "ranking": ranking, "recommended": best,
            "explanation": text, "gantt": runs[best]["gantt"]}
