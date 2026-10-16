"""OS demo server: Python standard library only (no pip, no MySQL). Run from the backend folder:
    python -m demo.server        then open http://127.0.0.1:8000
It serves demo/index.html and a small JSON API that calls the real OS modules in app/services."""
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.services import bankers_algorithm as bk
from app.services.adaptive_scheduler import DEFAULT_WEIGHTS, evaluate
from app.services.cluster_sim import simulate
from app.services.priority_aging import priority_with_aging
from app.services.scheduling_algorithms import Proc, fcfs, priority_np, round_robin, sjf

HERE = Path(__file__).parent

PRESETS = {
    "workloads": {
        "A: one long job + many short (long arrives first)": "P1,0,40,5\n" + "\n".join(f"S{i},1,{2 + i % 3},5" for i in range(6)),
        "B: equal-length jobs, same arrival": "\n".join(f"J{i},0,5,5" for i in range(6)),
        "C: long batch jobs + short interactive jobs": "\n".join(f"L{i},0,{24 + i},5" for i in range(5)) + "\n" + "\n".join(f"I{i},2,3,5" for i in range(4)),
        "D: priorities + deadlines": "Low1,0,10,9,60\nLow2,0,12,8,70\nCrit,1,8,1,12\nHi,2,6,2,18\nMid,3,9,5,40",
        "Textbook (P1..P4)": "P1,0,5,2\nP2,1,3,1\nP3,2,8,4\nP4,3,6,3",
    },
    "cluster": "A,0,5,5,3,2048,500\nB,0,3,5,2,1024,200\nC,0,2,5,1,512,100\nD,1,4,2,4,4096,800\nE,2,3,1,2,2048,300",
    "banker": {"available": [3, 3, 2],
               "max": {"P0": [7, 5, 3], "P1": [3, 2, 2], "P2": [9, 0, 2], "P3": [2, 2, 2], "P4": [4, 3, 3]},
               "allocation": {"P0": [0, 1, 0], "P1": [2, 0, 0], "P2": [3, 0, 2], "P3": [2, 1, 1], "P4": [0, 0, 2]}},
    "weights": DEFAULT_WEIGHTS,
}


def to_procs(rows):
    out = []
    for r in rows:
        dl = r.get("deadline")
        out.append(Proc(str(r["pid"]), int(r["arrival"]), int(r["burst"]), int(r.get("priority", 5)),
                        int(dl) if dl not in (None, "", 0) else None))
    return out


def api(path, d):
    if path == "/api/schedule":
        procs, algo = to_procs(d["jobs"]), d["algorithm"]
        if algo == "RR":
            return round_robin(procs, int(d.get("quantum", 4)))
        if algo == "PRIORITY_AGING":
            return priority_with_aging(procs, int(d.get("aging_interval", 10)))
        return {"FCFS": fcfs, "SJF": sjf, "PRIORITY": priority_np}[algo](procs)
    if path == "/api/recommend":
        w = d.get("weights") or None
        return evaluate(to_procs(d["jobs"]), int(d.get("quantum", 4)), {k: float(v) for k, v in w.items()} if w else None)
    if path == "/api/cluster":
        jobs = [{k: int(v) if k != "pid" else str(v) for k, v in j.items()} for j in d["jobs"]]
        h = d["host"]
        return simulate(jobs, int(h["cpu"]), int(h["ram"]), int(h["storage"]), d["policy"], int(d.get("aging_interval") or 10))
    if path == "/api/banker":
        mx, al, av = d["max"], d["allocation"], d["available"]
        safe, seq = bk.is_safe(av, mx, al)
        out = {"need": bk.need_matrix(mx, al), "safe": safe, "safe_sequence": seq}
        if d.get("pid"):
            dec = bk.request_resources(d["pid"], d["request"], av, mx, al)
            out["decision"] = {"status": dec.status, "reason": dec.reason, "safe_sequence": dec.safe_sequence}
        return out
    if path == "/api/aging_demo":
        iv = int(d.get("interval", 10))
        stream = [Proc("L", 0, 1, 10)] + [Proc(f"H{i}", 5 * i, 5, 1) for i in range(100)]
        a, b = priority_np(stream), priority_with_aging(stream, iv)
        pick = lambda r: {"completion": r["per_process"]["L"]["completion"], "waiting": r["per_process"]["L"]["waiting"]}
        return {"interval": iv, "without_aging": pick(a), "with_aging": pick(b), "stream_ends_at": a["makespan"]}
    raise KeyError(path)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/presets":
            self._send(200, PRESETS)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 2) or b"{}")
            self._send(200, api(self.path, data))
        except KeyError as e:
            self._send(404, {"error": f"unknown endpoint or missing field: {e}"})
        except (ValueError, TypeError, AssertionError) as e:
            self._send(400, {"error": str(e)})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"CloudSched OS demo on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    webbrowser.open(f"http://127.0.0.1:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
