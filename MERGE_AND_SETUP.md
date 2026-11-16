# What the merge changed

* **One server.** `demo/server.py` (stdlib OS demo) and `static/dbms_dashboard.html` are gone. The OS demo logic lives in
  `app/services/os_lab.py` and is exposed at `/api/os/*`; one UI (`static/index.html`) covers OS + DBMS.
* **OS ↔ DBMS bridge** (`app/api/bridge.py`, `app/services/bridge.py`)
  * `GET /api/bridge/recommend-policy`, `POST /api/bridge/apply-recommended` — OS algorithms on the live waiting queue.
  * `POST /api/bridge/compare` — adaptive-scheduler result stored in `comparison_runs` / `algorithm_results`
    (these tables were previously only filled by seed data).
  * `GET /api/bridge/banker/{job_id}` — Python Banker's vs `sp_bankers_check`; they must agree.
* **Execution + automation** (`executor.py`, `automation.py`, `/api/automation/*`). This also fixes a gap in the old flow:
  `sp_dispatch_waiting` started jobs but never placed them in a VM, and nothing noticed when a job finished.
* **Packaging:** `docker-compose.yml` (+ `docker-compose.docker.yml` for real containers), `backend/Dockerfile`, CI workflow, Makefile.
* `database/schema.sql` is still an unused duplicate of `01_schema.sql`; delete it if you do not need it.
