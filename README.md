# CloudSched — OS scheduling × MySQL DBMS × Docker

One application that combines the two halves of the project:

* **OS side** — FCFS / SJF / Priority / Priority-with-aging / Round Robin, the adaptive scheduler, the multi-resource
  cluster simulator and the Banker's algorithm (`backend/app/services/*`, pure Python).
* **DBMS side** — MySQL 8 schema, views, stored procedures (admission control, Banker's safety in SQL, dispatch,
  best-fit VM placement), triggers and row-level locking (`database/`).
* **Merged** — the OS algorithms now run on live database data and write their results back, and jobs can run as
  **real Docker containers** with an automation loop that drives the whole lifecycle.

```
 browser (static/index.html)
        │ REST
 FastAPI ── /api/os/*          OS lab (no database needed)
        ├── /api/jobs, /api/dbms/*   DBMS workflow → stored procedures / views / triggers
        ├── /api/bridge/*      OS algorithms on LIVE DB data + saving OS results to MySQL
        └── /api/automation/*  background loop: reap → dispatch → place in VM → launch container
                                   │ executor: simulate (default) | docker (real containers, cpu/memory limits)
                               MySQL 8 (host resources, jobs, allocations, VMs, containers, history, comparisons)
```

## Quick start (Docker — recommended)
```bash
cp .env.example .env            # set MYSQL_ROOT_PASSWORD
docker compose up --build       # MySQL (schema + seed auto-installed) + API + UI
```
Open <http://localhost:8000> (UI) and <http://localhost:8000/docs> (API). Jobs are **simulated** by default.
Run them as **real containers**:
```bash
docker pull python:3.12-alpine
docker compose -f docker-compose.yml -f docker-compose.docker.yml up --build
```
`docker compose down -v` wipes the database volume (the schema/seed re-install on the next start).

## Quick start (without Docker)
1. MySQL 8.0.16+ : `cd database && mysql -u root -p < install.sql` (**resets** the `cloudsched` database).
2. `cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
3. `cp .env.example .env`, put your MySQL password in `DATABASE_URL`, then `uvicorn app.main:app --reload`.

The OS lab works even if MySQL is down (the UI shows "MySQL offline" for live data only).

## What to try in the UI
| Tab | What it shows |
|---|---|
| Overview | live host usage, VMs with their containers, running jobs, waiting queue, trigger-written history |
| Jobs | submit → Allocate (Banker's check + VM placement) → Complete/Fail; **Banker check** runs the Python *and* the SQL Banker's on the same job and tells you whether they agree; **Recommend a policy** replays the live queue through the simulator under each dispatch policy |
| Scheduler lab | Gantt charts, adaptive "compare & recommend", **save the comparison to MySQL**, starvation/aging demo |
| Cluster & Banker | multi-job CPU/RAM/storage simulation, Banker's safe-state and request decisions |
| Analytics | `v_algorithm_comparison`, `v_job_metrics`, per-user summary |

Header buttons: **Start automation** (background loop with the chosen dispatch policy) and **Run one cycle**.

## Execution & automation
A cycle (`app/services/automation.py`): finished jobs are released (`sp_release_job`, triggers free the resources) →
waiting jobs are dispatched (`sp_dispatch_waiting`) → started jobs are placed best-fit into a RUNNING VM
(`sp_place_job_on_vm`) → containers are launched. `EXECUTOR_MODE=docker` launches real containers with
`--cpus = job.cpu_cores`, `--memory = job.ram_mb` (no swap), no network, all capabilities dropped, read-only root fs.
A job never carries user code: each `job_type` maps to a fixed built-in workload that runs for `est_time × TIME_SCALE` seconds.
A container killed by the kernel (OOM → exit 137) marks its job FAILED.

## Tests
```bash
cd backend && pytest -q                      # OS algorithms, API (SQLite), workloads, executors, bridge
MYSQL_TEST_URL=mysql+pymysql://root:PW@127.0.0.1:3306/cloudsched pytest -v tests/test_automation_mysql.py tests/test_mysql_concurrency.py
```
The MySQL tests need a freshly installed database; `test_automation_mysql.py` completes the seed jobs.

## Limitations (read before the viva)
* "VMs" are **logical partitions** of the host stored in MySQL (hypervisor model); only the containers are real. No hypervisor is launched.
* Time is abstract simulation units; `TIME_SCALE` maps a unit to real seconds. Banker's models only the three declared resources.
* Docker mode mounts the Docker socket into the API container = root-equivalent on that host. Dev machine / VM only.
* If Docker cannot start a container the job stays RUNNING with a CREATED container and is retried every cycle; the error is shown in the UI header.
* No authentication: anyone who can reach port 8000 can submit and run jobs.
