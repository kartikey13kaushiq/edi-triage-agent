"""Langfuse tracing with a no-op fallback.

When ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` are set, every run produces one trace:
an ``agent`` span per incident, a span per graph node, a ``tool`` span per tool call and a
``generation`` per model call carrying token usage and cost. Without the keys, tracing is a
no-op so tests and offline evals have no external dependency.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator


class _NoopObservation:
    def update(self, **_: Any) -> None:
        pass


class Tracer:
    def __init__(self) -> None:
        self._client = None
        if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
            from langfuse import get_client

            self._client = get_client()

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @contextmanager
    def observe(self, name: str, as_type: str = "span", **kwargs: Any) -> Iterator[Any]:
        if self._client is None:
            yield _NoopObservation()
            return
        with self._client.start_as_current_observation(name=name, as_type=as_type, **kwargs) as obs:
            yield obs

    def flush(self) -> None:
        if self._client is not None:
            self._client.flush()


@lru_cache(maxsize=1)
def get_tracer() -> Tracer:
    return Tracer()
