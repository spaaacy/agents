"""Runs queued jobs: a few worker threads, highest priority first, each plugin under its own concurrency cap."""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from .plugin import JobContext
from .store import JobResult

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger("hub.scheduler")


class Scheduler:
    def __init__(self, hub: Hub, slots: int = 2) -> None:
        self.hub = hub
        self.slots = max(1, slots)
        self.cancels: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.wakeup = threading.Event()

    def start(self) -> None:
        for n in range(self.slots):
            threading.Thread(target=self._loop, name=f"worker-{n}", daemon=True).start()

    def cancel(self, job_id: str) -> bool:
        """Stop a running job (its agent process is killed), or cancel a queued one."""
        with self.lock:
            event = self.cancels.get(job_id)
        if event:
            event.set()
            return True
        return self.hub.store.cancel(job_id)

    def running(self) -> list[str]:
        with self.lock:
            return list(self.cancels)

    def _loop(self) -> None:
        while not self.hub.stopping.is_set():
            job = self._claim()
            if job is None:
                self.wakeup.wait(2)
                self.wakeup.clear()
                continue
            self._run(job)

    def _claim(self):
        plugins = self.hub.plugins
        blocked = {name for name, plugin in plugins.items() if plugin.paused}
        blocked |= {name for name in self.hub.store.counts() if name not in plugins}  # a plugin no longer configured
        limits = {name: plugin.max_concurrent for name, plugin in plugins.items()}
        with self.lock:
            return self.hub.store.claim(blocked, limits)

    def _run(self, job) -> None:
        plugin = self.hub.plugins[job.plugin]
        if plugin.paused:  # paused between reading the pauses and claiming: put it back
            self.hub.store.requeue(job.id)
            return
        run_dir = self.hub.state_dir / "runs" / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        cancel = threading.Event()
        with self.lock:
            self.cancels[job.id] = cancel
        started = time.time()
        log.info("running %s (%s): %s", job.id, job.plugin, job.title)
        try:
            result = plugin.handle(job, JobContext(self.hub, plugin, job, run_dir, cancel))
            if not isinstance(result, JobResult):
                result = JobResult(status="failed", error=f"{job.plugin} returned {type(result).__name__}, not a JobResult")
        except Exception as exc:  # noqa: BLE001 - a job's failure is recorded, never fatal
            log.exception("%s failed", job.id)
            result = JobResult(status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
        finally:
            with self.lock:
                self.cancels.pop(job.id, None)
        if cancel.is_set():
            result.status, result.error = "cancelled", result.error or "cancelled"
        self.hub.store.finish(job.id, result)
        log.info("finished %s: %s in %.0fs%s", job.id, result.status, time.time() - started,
                 f" ({result.error})" if result.error else "")
        self.hub.publish({"type": "job", "id": job.id, "status": result.status})
