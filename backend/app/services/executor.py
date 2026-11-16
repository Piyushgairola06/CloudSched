"""Job execution backends: what "RUNNING" actually means for a job.

* SimExecutor    - nothing real is started; a job counts as finished once est_time * time_scale seconds
                   have passed since its container row was marked RUNNING. Needs no Docker.
* DockerExecutor - runs the job as a real Docker container with --cpus / --memory limits taken from the job.

SAFETY: a job never carries user code. Each job_type maps to a FIXED, built-in workload (below); only validated
integers (seconds, process count, MB) are substituted into it. Containers get no network, no capabilities and a
read-only root filesystem. Still, mounting /var/run/docker.sock into the API gives it root-equivalent control of
the host Docker daemon: use docker mode on a dev machine / VM, not on a shared server.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

log = logging.getLogger("cloudsched.executor")


@dataclass(frozen=True)
class ExecJob:
    job_id: int
    job_type: str
    cpu: int
    ram_mb: int
    est_time: int
    elapsed_s: float = 0.0          # seconds since the job was started (used by SimExecutor only)


def duration_seconds(est_time: int, time_scale: float) -> float:
    return max(1.0, float(est_time) * float(time_scale))


# ---- fixed workloads ------------------------------------------------------------------------------------
_CPU = """import time, multiprocessing as mp
mp.set_start_method("fork")
def spin(end):
    while time.time() < end:
        sum(i * i for i in range(5000))
if __name__ == "__main__":
    end = time.time() + __SECS__
    ps = [mp.Process(target=spin, args=(end,)) for _ in range(__NPROC__)]
    [p.start() for p in ps]
    [p.join() for p in ps]
"""
_MEM = """import time
buf = bytearray(__MB__ * 1024 * 1024)
for i in range(0, len(buf), 4096):
    buf[i] = 1
time.sleep(__SECS__)
"""
_IO = """import os, time
end = time.time() + __SECS__
data = os.urandom(1024 * 1024)
while time.time() < end:
    with open("/tmp/io.bin", "wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    time.sleep(0.05)
"""
_MIXED = """import time
buf = bytearray(__MB__ * 1024 * 1024)
for i in range(0, len(buf), 4096):
    buf[i] = 1
end = time.time() + __SECS__
while time.time() < end:
    sum(i * i for i in range(5000))
"""
_TEMPLATES = {"CPU_BOUND": _CPU, "MEMORY_BOUND": _MEM, "IO_BOUND": _IO, "MIXED": _MIXED}


def workload_command(job: ExecJob, time_scale: Optional[float] = None) -> list[str]:
    """argv for the container. Raises ValueError for an unknown job_type."""
    if job.job_type not in _TEMPLATES:
        raise ValueError(f"no built-in workload for job_type {job.job_type!r}")
    secs = int(round(duration_seconds(job.est_time, settings.time_scale if time_scale is None else time_scale)))
    nproc = max(1, min(int(job.cpu), 64))
    mb = max(1, int(int(job.ram_mb) * (0.25 if job.job_type == "MIXED" else 0.6)))
    code = (_TEMPLATES[job.job_type]
            .replace("__SECS__", str(secs)).replace("__NPROC__", str(nproc)).replace("__MB__", str(mb)))
    return ["python", "-c", code]


# ---- backends -------------------------------------------------------------------------------------------
class SimExecutor:
    name = "simulate"

    def __init__(self, time_scale: Optional[float] = None):
        self.time_scale = settings.time_scale if time_scale is None else time_scale

    def start(self, job: ExecJob) -> Optional[str]:
        return None                                   # no container id: nothing real was started

    def poll(self, ref: Optional[str], job: ExecJob) -> Optional[int]:
        """None = still running, otherwise the exit code (0 = success)."""
        return 0 if job.elapsed_s >= duration_seconds(job.est_time, self.time_scale) else None

    def cleanup(self, ref: Optional[str]) -> None:
        pass


class DockerExecutor:
    name = "docker"

    def __init__(self, client=None, image: Optional[str] = None, not_found: Optional[type] = None):
        if client is None:
            try:
                import docker                          # pip install docker
                from docker.errors import NotFound
            except ImportError as exc:                 # pragma: no cover
                raise RuntimeError("executor_mode=docker needs the 'docker' package: pip install docker") from exc
            client = docker.from_env()
            client.ping()                              # fail early with a clear error if the daemon is unreachable
            not_found = NotFound
        self.client = client
        self.image = image or settings.docker_image
        self._not_found = not_found or KeyError

    def start(self, job: ExecJob) -> str:
        name = f"cloudsched-job-{job.job_id}"
        self.cleanup(name)                             # a stale container from an earlier run would block the name
        c = self.client.containers.run(
            self.image, command=workload_command(job), detach=True, name=name,
            labels={"cloudsched.job_id": str(job.job_id)},
            nano_cpus=int(job.cpu * 1_000_000_000),
            mem_limit=f"{job.ram_mb}m", memswap_limit=f"{job.ram_mb}m",     # no swap: the limit is a hard limit
            network_mode="none", cap_drop=["ALL"], security_opt=["no-new-privileges"],
            read_only=True, tmpfs={"/tmp": "size=16m"}, pids_limit=256,
        )
        return c.id

    def poll(self, ref: Optional[str], job: ExecJob) -> Optional[int]:
        try:
            c = self.client.containers.get(ref)
        except self._not_found:
            log.warning("container %s vanished; marking its job FAILED", ref)
            return 1
        c.reload()
        if c.status in ("exited", "dead"):
            return int(c.attrs["State"]["ExitCode"])   # 137 = killed (e.g. OOM) -> job FAILED
        return None

    def cleanup(self, ref: Optional[str]) -> None:
        if not ref:
            return
        try:
            self.client.containers.get(ref).remove(force=True)
        except self._not_found:
            pass
        except Exception:                               # pragma: no cover
            log.exception("could not remove container %s", ref)


_cache: dict = {}


def get_executor():
    """Executor for settings.executor_mode (cached). Raises RuntimeError with a readable message if unusable."""
    mode = settings.executor_mode.lower()
    if mode not in _cache:
        if mode == "simulate":
            _cache[mode] = SimExecutor()
        elif mode == "docker":
            _cache[mode] = DockerExecutor()
        else:
            raise RuntimeError(f"unknown EXECUTOR_MODE {mode!r} (use 'simulate' or 'docker')")
    return _cache[mode]
