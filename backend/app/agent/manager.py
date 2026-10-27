"""Runs agent loops on a bounded worker pool and keeps session history in memory."""
from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from ..config import Settings
from .loop import Agent
from .run import AgentRun, RunOptions

MAX_RUNS_KEPT = 100
MAX_RUNS_WITH_ROWS = 20


class RunManager:
    def __init__(self, settings: Settings, agent: Agent):
        self.settings = settings
        self.agent = agent
        self._runs: OrderedDict[str, AgentRun] = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=settings.max_concurrent_runs, thread_name_prefix="agent")

    def start(self, request: str, options: RunOptions, max_iterations: int | None = None) -> AgentRun:
        run = AgentRun(request=request, options=options,
                       max_iterations=max_iterations or self.settings.agent_max_iterations)
        with self._lock:
            self._runs[run.id] = run
            self._trim()
        self._pool.submit(self.agent.run, run)
        return run

    def _trim(self) -> None:
        while len(self._runs) > MAX_RUNS_KEPT:
            _, old = self._runs.popitem(last=False)
            old.cancel()
        for i, run in enumerate(reversed(list(self._runs.values()))):
            if i >= MAX_RUNS_WITH_ROWS and run.rows:
                run.rows = []  # keep the summary, free the memory; exports stay downloadable

    def get(self, run_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self) -> list[dict]:
        with self._lock:
            return [r.brief() for r in reversed(self._runs.values())]

    def shutdown(self) -> None:
        with self._lock:
            runs = list(self._runs.values())
        for r in runs:
            r.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)
