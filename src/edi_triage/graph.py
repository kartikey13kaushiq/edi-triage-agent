"""Supervisor-worker LangGraph for EDI failure triage.

    START -> supervisor --(route)--> {transport|envelope|certificate|partner_config}_worker
                ^                                   |
                +-------------- evidence -----------+
             supervisor --> diagnose (RAG) --> propose --> human_approval (interrupt) --> END
             supervisor --> escalate --> END

* The supervisor classifies once, then routes. Workers run a fixed, read-only tool plan for
  their category and report evidence. If a worker comes back clean, the supervisor reroutes
  to the next most likely category (bounded by ``max_hops``) - this is the cycle.
* Evidence beats the classifier: the category that produced confirming evidence becomes the
  final category.
* ``human_approval`` calls ``interrupt()``: the run is checkpointed and suspended until an
  operator resumes it with an ``ApprovalDecision``. Nothing in the graph writes to a partner
  or to production; an approved proposal is handed to a human to execute.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .llm import TriageLLM
from .models import (
    WORKER_FOR_CATEGORY,
    ApprovalDecision,
    Diagnosis,
    FailureCategory,
    Incident,
    TriageState,
)
from .observability import get_tracer
from .retrieval import Retriever
from .tools import Toolbox, ToolResult

WORKER_ORDER = [
    "transport_worker",
    "certificate_worker",
    "partner_config_worker",
    "envelope_worker",
]
CATEGORY_FOR_WORKER = {worker: cat for cat, worker in WORKER_FOR_CATEGORY.items()}


def _evidence(worker: str, result: ToolResult, finding: str | None = None) -> dict[str, Any]:
    return {"worker": worker, "finding": finding, **result.model_dump(mode="json")}


# ------------------------------------------------------------------------ workers


def transport_checks(tools: Toolbox, incident: Incident) -> list[dict]:
    worker = "transport_worker"
    evidence = []
    probe = tools.probe_endpoint(incident.partner_id)
    finding = None
    if probe.ok:
        data = probe.data or {}
        profile = tools.get_partner_profile(incident.partner_id).data or {}
        pinned, presented = profile.get("host_key_fingerprint"), data.get("host_key_fingerprint")
        if pinned and presented and pinned != presented:
            finding = f"SSH host key changed: server presents {presented}, profile pins {pinned}"
        elif data.get("reachable") is False:
            finding = f"endpoint handshake failed: {data.get('detail')}"
    evidence.append(_evidence(worker, probe, finding))
    return evidence


def envelope_checks(tools: Toolbox, incident: Incident) -> list[dict]:
    worker = "envelope_worker"
    parsed = tools.parse_x12_envelope(incident.payload_excerpt)
    finding = None
    if parsed.ok and parsed.data["issues"]:
        finding = "outbound envelope defect: " + "; ".join(parsed.data["issues"])
    evidence = [_evidence(worker, parsed, finding)]
    if incident.ack_excerpt:
        ack = tools.compare_acknowledgement(incident.payload_excerpt, incident.ack_excerpt)
        ack_finding = None
        if ack.ok and (not ack.data["matched"] or ack.data["status"] != "accepted"):
            ack_finding = f"{ack.data['ack_type']} ack {ack.data['status']}: " + "; ".join(ack.data["issues"])
        evidence.append(_evidence(worker, ack, ack_finding))
    return evidence


def certificate_checks(tools: Toolbox, incident: Incident) -> list[dict]:
    worker = "certificate_worker"
    result = tools.check_certificates(incident.partner_id, as_of=incident.occurred_at)
    finding = None
    if result.ok:
        bad = [c for c in result.data["certificates"] if c["status"] in {"expired", "not_yet_valid"}]
        if bad:
            finding = "; ".join(
                f"partner {c['usage']} certificate {c['fingerprint']} is {c['status'].replace('_', ' ')} "
                f"at incident time (valid {c['not_before'][:10]} to {c['not_after'][:10]})"
                for c in bad
            )
    return [_evidence(worker, result, finding)]


def partner_config_checks(tools: Toolbox, incident: Incident) -> list[dict]:
    worker = "partner_config_worker"
    result = tools.check_identity_config(incident.partner_id, incident.log_excerpt, incident.payload_excerpt)
    finding = None
    if result.ok and result.data["mismatches"]:
        finding = "partner profile mismatch: " + "; ".join(result.data["mismatches"])
    return [_evidence(worker, result, finding)]


WORKER_CHECKS = {
    "transport_worker": transport_checks,
    "envelope_worker": envelope_checks,
    "certificate_worker": certificate_checks,
    "partner_config_worker": partner_config_checks,
}


# -------------------------------------------------------------------------- graph


def _traced(name: str, fn, as_type: str = "span"):
    def node(state: TriageState):
        with get_tracer().observe(name, as_type=as_type) as obs:
            out = fn(state)
            obs.update(output=getattr(out, "goto", None) or out)
            return out

    node.__name__ = name
    node.__annotations__ = getattr(fn, "__annotations__", {})
    return node


def build_graph(
    llm: TriageLLM,
    tools: Toolbox,
    retriever: Retriever,
    checkpointer: BaseCheckpointSaver | None = None,
    max_hops: int = 4,
    top_k: int = 4,
):
    def supervisor(
        state: TriageState,
    ) -> Command[
        Literal[
            "transport_worker",
            "envelope_worker",
            "certificate_worker",
            "partner_config_worker",
            "diagnose",
            "escalate",
        ]
    ]:
        incident = Incident.model_validate(state["incident"])
        visited = state.get("visited_workers", [])

        if "classification" not in state:
            cls = llm.classify(incident)
            first = WORKER_FOR_CATEGORY.get(cls.category, WORKER_ORDER[0])
            return Command(
                goto=first,
                update={
                    "classification": {**cls.model_dump(mode="json"), "initial_category": cls.category.value},
                    "status": "triaging",
                },
            )

        # Evidence from the latest worker decides the final category.
        confirming = [e for e in state.get("evidence", []) if e.get("finding")]
        if confirming:
            confirmed = CATEGORY_FOR_WORKER[confirming[-1]["worker"]]
            cls = dict(state["classification"])
            if cls["category"] != confirmed.value:
                cls["rationale"] += (
                    f" | reclassified to {confirmed.value} by {confirming[-1]['worker']} evidence"
                )
                cls["category"] = confirmed.value
            return Command(goto="diagnose", update={"classification": cls})

        if len(visited) >= max_hops:
            return Command(goto="escalate")

        hint = state.get("reroute_hint")
        candidates = ([hint] if hint else []) + WORKER_ORDER
        nxt = next((w for w in candidates if w not in visited), None)
        return Command(goto=nxt or "escalate", update={"reroute_hint": None})

    def make_worker(name: str):
        def worker(state: TriageState) -> Command[Literal["supervisor"]]:
            incident = Incident.model_validate(state["incident"])
            evidence = WORKER_CHECKS[name](tools, incident)
            update: dict[str, Any] = {"visited_workers": [name], "evidence": evidence}
            if not any(e.get("finding") for e in evidence):
                secondary = state["classification"].get("secondary")
                if secondary and secondary in {c.value for c in WORKER_FOR_CATEGORY}:
                    update["reroute_hint"] = WORKER_FOR_CATEGORY[FailureCategory(secondary)]
            return Command(goto="supervisor", update=update)

        worker.__name__ = name
        return worker

    def diagnose(state: TriageState) -> dict:
        incident = Incident.model_validate(state["incident"])
        category = FailureCategory(state["classification"]["category"])
        findings = [e["finding"] for e in state.get("evidence", []) if e.get("finding")]
        query = " ".join([category.value.replace("_", " "), incident.error_message, *findings])
        retrieved = [s.to_dict() for s in retriever.search(query, k=top_k)]
        diagnosis = llm.diagnose(incident, category, state.get("evidence", []), retrieved)
        return {"retrieved": retrieved, "diagnosis": diagnosis.model_dump(mode="json")}

    def propose(state: TriageState) -> dict:
        incident = Incident.model_validate(state["incident"])
        category = FailureCategory(state["classification"]["category"])
        diagnosis = Diagnosis.model_validate(state["diagnosis"])
        proposal = llm.propose(incident, category, diagnosis, state.get("retrieved", []))
        return {"proposal": proposal.model_dump(mode="json"), "status": "awaiting_approval"}

    def human_approval(state: TriageState) -> dict:
        # Everything before interrupt() re-runs on resume, so this node has no side effects.
        raw = interrupt(
            {
                "incident_id": state["incident"]["incident_id"],
                "category": state["classification"]["category"],
                "diagnosis": state["diagnosis"],
                "proposal": state["proposal"],
            }
        )
        decision = ApprovalDecision.model_validate(raw)
        return {
            "decision": decision.model_dump(mode="json"),
            "status": "approved" if decision.approved else "rejected",
        }

    def escalate(state: TriageState) -> dict:
        return {
            "status": "escalated",
            "diagnosis": {
                "root_cause": "no worker found confirming evidence",
                "explanation": "escalated to L3 with all collected evidence",
                "cited_runbooks": [],
                "confidence": 0.0,
            },
        }

    graph = StateGraph(TriageState)
    graph.add_node("supervisor", _traced("supervisor", supervisor))
    for name in WORKER_CHECKS:
        graph.add_node(name, _traced(name, make_worker(name)))
    graph.add_node("diagnose", _traced("diagnose", diagnose, as_type="retriever"))
    graph.add_node("propose", _traced("propose", propose))
    graph.add_node("human_approval", human_approval)  # interrupt() must not run inside a span
    graph.add_node("escalate", _traced("escalate", escalate))

    graph.add_edge(START, "supervisor")
    graph.add_edge("diagnose", "propose")
    graph.add_edge("propose", "human_approval")
    graph.add_edge("human_approval", END)
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=checkpointer)


def initial_state(incident: Incident) -> TriageState:
    return {"incident": incident.model_dump(mode="json"), "visited_workers": [], "evidence": []}
