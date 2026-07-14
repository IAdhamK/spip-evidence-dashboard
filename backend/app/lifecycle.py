from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI


class WorkerManager(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...


def analysis_lifespan(manager: WorkerManager, enabled: bool):
    """Create an application lifespan that owns the worker manager exactly once."""

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if enabled:
            manager.start()
        try:
            yield
        finally:
            manager.stop()

    return lifespan
