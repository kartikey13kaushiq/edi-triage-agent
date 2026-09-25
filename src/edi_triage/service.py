"""Application service: start a triage run, inspect it, and resume it with a human decision."""

from __future__ import annotations

import time
import uuid
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from .checkpoint import make_checkpointer
from .graph import build_graph, initial_state
from .llm import TriageLLM, make_llm
from .models import ApprovalDecision, Incident
from .observability import get_tracer
from .retrieval import Retriever, default_retriever
from .tools import Toolbox, default_toolbox


class TriageService:
    def __init__(
        self,
        llm: TriageLLM | None = None,
        tools: Toolbox | None = None,
        retriever: Retriever | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
        max_hops: int = 4,
    ):
        self.llm = llm or make_llm()
        self.graph = build_graph(
            self.llm,
            tools or default_toolbox(),
            retriever or default_retriever(),
            checkpointer or make_checkpointer(),
            max_hops=max_hops,
        )

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}

    def _invoke(self, payload: Any, thread_id: str, name: str) -> dict[str, Any]:
        tracer = get_tracer()
        with tracer.observe(name, as_type="agent", metadata={"thread_id": thread_id}) as span:
            started = time.perf_counter()
            self.graph.invoke(payload, self._config(thread_id))
            view = self.get(thread_id)
            view["latency_s"] = round(time.perf_counter() - started, 3)
            span.update(output={k: view.get(k) for k in ("status", "classification", "pending_approval")})
        tracer.flush()
        return view

    def start(self, incident: Incident, thread_id: str | None = None) -> dict[str, Any]:
        thread_id = thread_id or f"{incident.incident_id}-{uuid.uuid4().hex[:8]}"
        return self._invoke(initial_state(incident), thread_id, "edi-triage")

    def decide(self, thread_id: str, decision: ApprovalDecision) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(thread_id))
        if not snapshot.interrupts:
            raise LookupError(f"thread {thread_id!r} is not awaiting approval")
        return self._invoke(Command(resume=decision.model_dump()), thread_id, "edi-triage-decision")

    def get(self, thread_id: str) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(thread_id))
        if not snapshot.values:
            raise KeyError(thread_id)
        values = dict(snapshot.values)
        values["thread_id"] = thread_id
        values["pending_approval"] = snapshot.interrupts[0].value if snapshot.interrupts else None
        return values
