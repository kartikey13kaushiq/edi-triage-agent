"""Typed contracts shared by every node, tool and LLM backend in the triage agent."""

from __future__ import annotations

import operator
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


class FailureCategory(str, Enum):
    TRANSPORT_HANDSHAKE = "transport_handshake"  # AS2 HTTP/TLS or SFTP SSH handshake failures
    ENVELOPE_ACK_MISMATCH = "envelope_ack_mismatch"  # ISA/GS/ST control numbers, 997/999/TA1, MDN
    CERTIFICATE_EXPIRY = "certificate_expiry"  # expired / not-yet-valid / rotated certificates
    PARTNER_MISCONFIG = "partner_misconfig"  # wrong AS2 IDs, URLs, credentials, directories
    UNKNOWN = "unknown"


# Each diagnosis worker owns exactly one category.
WORKER_FOR_CATEGORY: dict[FailureCategory, str] = {
    FailureCategory.TRANSPORT_HANDSHAKE: "transport_worker",
    FailureCategory.ENVELOPE_ACK_MISMATCH: "envelope_worker",
    FailureCategory.CERTIFICATE_EXPIRY: "certificate_worker",
    FailureCategory.PARTNER_MISCONFIG: "partner_config_worker",
}


class Incident(BaseModel):
    """A failed B2B/EDI transfer as reported by the integration platform."""

    incident_id: str
    partner_id: str
    protocol: Literal["AS2", "SFTP", "FTPS", "HTTPS"]
    direction: Literal["inbound", "outbound"] = "outbound"
    error_message: str
    log_excerpt: str = ""
    payload_excerpt: str = Field(
        default="", description="Raw X12/EDIFACT envelope of the failed interchange, if available."
    )
    ack_excerpt: str = Field(default="", description="Received 997/999/TA1 or MDN, if any.")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Classification(BaseModel):
    category: FailureCategory
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class Diagnosis(BaseModel):
    root_cause: str = Field(description="One-sentence root cause grounded in the evidence.")
    explanation: str
    cited_runbooks: list[str] = Field(
        default_factory=list, description="Runbook chunk ids that support this diagnosis."
    )
    confidence: float = Field(ge=0.0, le=1.0)


class RemediationStep(BaseModel):
    action: str
    owner: Literal["our_team", "partner", "either"]
    reversible: bool


class RemediationProposal(BaseModel):
    summary: str
    steps: list[RemediationStep]
    risk: Literal["low", "medium", "high"]


class ApprovalDecision(BaseModel):
    approved: bool
    reviewer: str
    comment: str = ""


class TriageState(TypedDict, total=False):
    """LangGraph state. Values are plain JSON so the Postgres checkpointer can persist them."""

    incident: dict[str, Any]
    classification: dict[str, Any]
    route: str
    visited_workers: Annotated[list[str], operator.add]
    evidence: Annotated[list[dict[str, Any]], operator.add]
    reroute_hint: str | None
    retrieved: list[dict[str, Any]]
    diagnosis: dict[str, Any]
    proposal: dict[str, Any]
    decision: dict[str, Any]
    status: Literal["triaging", "awaiting_approval", "approved", "rejected", "escalated"]
