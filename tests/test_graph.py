import pytest
from langgraph.checkpoint.memory import InMemorySaver

from edi_triage.llm import HeuristicTriageLLM
from edi_triage.models import ApprovalDecision
from edi_triage.service import TriageService


def test_pauses_at_approval_gate_then_approves(service, incident):
    view = service.start(incident("INC-009"))
    assert view["status"] == "awaiting_approval"
    assert view["classification"]["category"] == "certificate_expiry"
    assert view["pending_approval"]["proposal"]["steps"]
    assert "decision" not in view

    final = service.decide(view["thread_id"], ApprovalDecision(approved=True, reviewer="ops"))
    assert final["status"] == "approved"
    assert final["decision"]["reviewer"] == "ops"
    assert final["pending_approval"] is None


def test_rejection_is_recorded(service, incident):
    view = service.start(incident("INC-016"))
    final = service.decide(view["thread_id"], ApprovalDecision(approved=False, reviewer="ops", comment="no"))
    assert final["status"] == "rejected"


def test_evidence_overrides_misleading_error_text(service, incident):
    # Text says "authentication-failed" (partner config); the cert worker finds a future-dated cert.
    view = service.start(incident("INC-013"))
    assert view["classification"]["initial_category"] == "partner_misconfig"
    assert view["classification"]["category"] == "certificate_expiry"
    assert view["visited_workers"][0] == "partner_config_worker"
    assert view["visited_workers"][-1] == "certificate_worker"
    assert "reclassified" in view["classification"]["rationale"]


def test_hop_budget_bounds_the_cycle(incident):
    service = TriageService(llm=HeuristicTriageLLM(), checkpointer=InMemorySaver(), max_hops=1)
    view = service.start(incident("INC-013"))
    assert view["status"] == "escalated"
    assert len(view["visited_workers"]) == 1


def test_no_fault_found_escalates(service, incident):
    view = service.start(incident("INC-032"))
    assert view["status"] == "escalated"
    assert len(view["visited_workers"]) == 4


def test_cannot_decide_a_thread_that_is_not_waiting(service, incident):
    view = service.start(incident("INC-032"))
    with pytest.raises(LookupError):
        service.decide(view["thread_id"], ApprovalDecision(approved=True, reviewer="ops"))


def test_run_resumes_from_checkpoint_in_a_new_service_instance(incident):
    """Simulates a restart: a fresh service (new graph, new LLM) resumes via the shared checkpointer."""
    saver = InMemorySaver()
    first = TriageService(llm=HeuristicTriageLLM(), checkpointer=saver)
    view = first.start(incident("INC-025"))
    assert view["status"] == "awaiting_approval"

    second = TriageService(llm=HeuristicTriageLLM(), checkpointer=saver)
    final = second.decide(view["thread_id"], ApprovalDecision(approved=True, reviewer="ops"))
    assert final["status"] == "approved"
    assert final["classification"]["category"] == "partner_misconfig"
