# OS demo – how to run and present (no MySQL, no pip needed)

```
cd cloudsched\backend
python -m demo.server            # opens http://127.0.0.1:8000
python -m demo.server 9000       # different port if 8000 is busy
```
Tests (pure OS modules need only `pip install pytest`):  `pytest tests/test_os_core.py tests/test_os_extra.py`

## 7-minute demo script
1. **Scheduling (tab 1).** Preset "Textbook (P1..P4)". Run FCFS, SJF, Round Robin (quantum 2). Show the Gantt and the table.
   Expected: avg waiting FCFS 5.75, SJF 5.25, RR(q=2) 9.75 – RR is worse on waiting but best on response (2.0).
2. **Idle CPU.** Edit the workload to `A,0,2,5` / `B,5,2,5` -> Gantt shows an IDLE gap 2–5, CPU util 57%.
3. **Adaptive.** "Run on every preset": different mixes -> different recommendations (RR / FCFS / SJF). Then run
   preset A with "Compare & recommend": SJF has low waiting but is NOT chosen; read the explanation. Change a weight and re-run.
4. **Resources (tab 2).** Default workload, FCFS: jobs wait when CPU/RAM are short, backfill lets small jobs run; show CPU/RAM % per step.
5. **Banker's (tab 3).** Textbook state is SAFE. Request P1 `1,0,2` -> GRANTED. P4 `3,3,0` -> DEFERRED_UNSAFE. P0 `5,0,0` -> DEFERRED_UNAVAILABLE. P1 `4,0,0` -> INVALID.
6. **Aging (tab 4).** Without aging L finishes at t=501 (dead last); with interval 10 at t=91.

## Viva cheat-sheet
* **Why not always SJF?** SJF minimises average waiting but can starve long jobs and ignores priorities/deadlines; the score includes worst-case wait, response, priority-weighted wait and deadline misses.
* **Normalisation:** each metric is rescaled to 0 (best candidate) – 1 (worst) so units don't dominate; weights are configurable and renormalised to sum to 1.
* **Banker's:** safe state = a completion order exists where every job can finish with what is free + what finished jobs return. Granting a request is allowed only if the resulting state is safe.
* **Backfilling starvation:** a big job can be bypassed forever by small ones; PRIORITY_AGING reserves resources for the top-ranked (aged) job.
* **Resource invariants:** available >= 0, allocated <= total, one allocation per job, release exactly once (allocated is derived from the allocation table, so counters cannot drift).
* **Tie-breaks:** FCFS (arrival,pid); SJF (burst,arrival,pid); Priority (priority,arrival,pid); smaller number = higher priority.

## Honest limitations
Simulation on abstract time units. Round Robin is evaluated single-CPU, not inside the multi-core resource simulation.
Banker's is not yet wired into the resource simulator's admission control (with max = request it is always safe).
No real VMs/containers/database in this demo; those are later phases. In my sample mixes PRIORITY was never recommended
(its cost on other metrics outweighed deadline gains) – the engine reports what the numbers say.
