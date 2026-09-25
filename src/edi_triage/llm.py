"""LLM backends for the three reasoning steps: classify, diagnose, propose.

``ClaudeTriageLLM`` uses the Anthropic Messages API with Pydantic structured outputs,
so every step returns a validated object instead of free text.
``HeuristicTriageLLM`` is a deterministic rule-based stand-in: it lets the whole graph,
the test-suite and the eval harness run offline, and it doubles as a second baseline.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from .models import (
    Classification,
    Diagnosis,
    FailureCategory,
    Incident,
    RemediationProposal,
    RemediationStep,
)
from .observability import get_tracer

T = TypeVar("T", bound=BaseModel)

# USD per million tokens (input, output). Used for per-run cost accounting in evals.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass
class UsageMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    by_step: dict[str, int] = field(default_factory=dict)

    def record(self, step: str, model: str, input_tokens: int, output_tokens: int, latency_s: float) -> None:
        price_in, price_out = PRICING.get(model, (0.0, 0.0))
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += (input_tokens * price_in + output_tokens * price_out) / 1_000_000
        self.latency_s += latency_s
        self.by_step[step] = self.by_step.get(step, 0) + 1


class ClassificationOutput(Classification):
    secondary: FailureCategory | None = None


class TriageLLM(Protocol):
    meter: UsageMeter

    def classify(self, incident: Incident) -> ClassificationOutput: ...

    def diagnose(
        self, incident: Incident, category: FailureCategory, evidence: list[dict], runbooks: list[dict]
    ) -> Diagnosis: ...

    def propose(
        self, incident: Incident, category: FailureCategory, diagnosis: Diagnosis, runbooks: list[dict]
    ) -> RemediationProposal: ...


# --------------------------------------------------------------------------- Claude

SYSTEM_PROMPT = """You are the triage agent for a B2B/EDI integration platform that exchanges X12 \
interchanges with trading partners over AS2 and SFTP. You classify failed transfers, diagnose \
their root cause from tool evidence and operational runbooks, and propose remediation for a \
human operator to approve.

Failure categories:
- transport_handshake: the connection itself failed (TLS/SSH handshake, timeouts, refused \
connections, host key or cipher/protocol mismatches) before any data was accepted.
- envelope_ack_mismatch: data was delivered but the X12 envelope or the 997/999/TA1 \
acknowledgement is wrong (control-number mismatches, rejected AK9/TA1, ack correlation failures).
- certificate_expiry: an AS2 signing/encryption certificate is expired, not yet valid, or was \
rotated so signatures or decryption fail.
- partner_misconfig: an identifier, URL, path, username or receiver id in the partner profile \
does not match what the partner expects.
- unknown: none of the above fits the evidence.

Ground every claim in the incident text, the tool evidence or the runbooks you are given. Cite \
runbook chunk ids exactly as provided. Remediation is only a proposal: it is executed by a \
human after approval, so mark steps that cannot be undone as reversible=false."""


class ClaudeTriageLLM:
    def __init__(self, model: str | None = None, client: Any | None = None):
        import anthropic

        self.model = model or os.environ.get("TRIAGE_MODEL", "claude-opus-5")
        self.client = client or anthropic.Anthropic()
        self.meter = UsageMeter()

    def _parse(self, step: str, prompt: str, schema: type[T], effort: str) -> T:
        with get_tracer().observe(step, as_type="generation", model=self.model, input=prompt) as gen:
            started = time.perf_counter()
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive"},
                output_config={"effort": effort},
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
            )
            usage = response.usage
            self.meter.record(
                step, self.model, usage.input_tokens, usage.output_tokens, time.perf_counter() - started
            )
            price_in, price_out = PRICING.get(self.model, (0.0, 0.0))
            gen.update(
                output=response.parsed_output.model_dump(mode="json") if response.parsed_output else None,
                usage_details={"input": usage.input_tokens, "output": usage.output_tokens},
                cost_details={
                    "input": usage.input_tokens * price_in / 1e6,
                    "output": usage.output_tokens * price_out / 1e6,
                },
            )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(
                f"{step}: model returned no structured output (stop_reason={response.stop_reason})"
            )
        return response.parsed_output

    @staticmethod
    def _incident_block(incident: Incident) -> str:
        return "<incident>\n" + incident.model_dump_json(indent=2) + "\n</incident>"

    def classify(self, incident: Incident) -> ClassificationOutput:
        prompt = (
            f"{self._incident_block(incident)}\n\nClassify this failure. Set `secondary` to the next "
            "most likely category if the evidence is ambiguous, otherwise null."
        )
        return self._parse("classify", prompt, ClassificationOutput, effort="low")

    def diagnose(self, incident, category, evidence, runbooks) -> Diagnosis:
        prompt = (
            f"{self._incident_block(incident)}\n\n<category>{category.value}</category>\n\n"
            f"<tool_evidence>\n{json.dumps(evidence, indent=2, default=str)}\n</tool_evidence>\n\n"
            f"<runbooks>\n{json.dumps(runbooks, indent=2)}\n</runbooks>\n\n"
            "Diagnose the root cause. Prefer tool evidence over the error text when they disagree."
        )
        return self._parse("diagnose", prompt, Diagnosis, effort="medium")

    def propose(self, incident, category, diagnosis, runbooks) -> RemediationProposal:
        prompt = (
            f"{self._incident_block(incident)}\n\n<category>{category.value}</category>\n\n<diagnosis>\n{diagnosis.model_dump_json(indent=2)}\n"
            f"</diagnosis>\n\n<runbooks>\n{json.dumps(runbooks, indent=2)}\n</runbooks>\n\n"
            "Propose the smallest remediation that resolves the root cause, following the runbooks."
        )
        return self._parse("propose", prompt, RemediationProposal, effort="medium")


# ------------------------------------------------------------------------ heuristic

_SIGNALS: dict[FailureCategory, list[str]] = {
    FailureCategory.CERTIFICATE_EXPIRY: [
        r"certificate (has )?expired",
        r"certificateexpired",
        r"not yet valid",
        r"decryption-failed",
        r"insufficient-message-security",
        r"cert(ificate)? .*(rotat|renew)",
        r"notafter",
    ],
    FailureCategory.ENVELOPE_ACK_MISMATCH: [
        r"\b(997|999|ta1)\b",
        r"\bak9\b",
        r"control number",
        r"acknowledg",
        r"\bse01\b",
        r"segment count",
        r"\biea\b",
        r"\bge02\b",
        r"ack (overdue|correlation)",
    ],
    FailureCategory.PARTNER_MISCONFIG: [
        r"unknown-trading-partner",
        r"\b404\b",
        r"permission denied",
        r"no such file",
        r"as2-(to|from)",
        r"receiver id",
        r"invalid (as2 )?identifier",
        r"authentication-failed",
    ],
    FailureCategory.TRANSPORT_HANDSHAKE: [
        r"handshake",
        r"timed? ?out",
        r"connection (refused|reset)",
        r"host key",
        r"pkix",
        r"protocol_version",
        r"no matching (key exchange|host key|cipher)",
        r"no route to host",
    ],
}

_TEMPLATES: dict[FailureCategory, list[tuple[str, str, bool]]] = {
    FailureCategory.TRANSPORT_HANDSHAKE: [
        ("Confirm the connectivity finding with the partner's technical contact", "partner", True),
        ("Partner fixes the endpoint (TLS/SSH policy, host key confirmation or allow-list)", "partner", True),
        ("Resend the failed interchanges from the retry queue", "our_team", False),
    ],
    FailureCategory.ENVELOPE_ACK_MISMATCH: [
        ("Fix envelope generation / control-number counter in the outbound map", "our_team", True),
        ("Regenerate the interchange with a new control number", "our_team", True),
        ("Resend the corrected interchange and watch for the functional ack", "our_team", False),
    ],
    FailureCategory.CERTIFICATE_EXPIRY: [
        ("Request the partner's current certificate and verify its fingerprint out of band", "partner", True),
        ("Schedule the certificate swap in the partner profile via change control", "our_team", True),
        ("Resend affected interchanges after the swap", "our_team", False),
    ],
    FailureCategory.PARTNER_MISCONFIG: [
        (
            "Confirm the correct identifier / endpoint / credentials with the partner in writing",
            "partner",
            True,
        ),
        ("Correct the partner profile via change control", "our_team", True),
        ("Resend the failed interchanges", "our_team", False),
    ],
    FailureCategory.UNKNOWN: [
        ("Escalate to L3 with the collected evidence", "our_team", True),
    ],
}


class HeuristicTriageLLM:
    """Keyword scoring + evidence templating. Deterministic and free."""

    model = "heuristic"

    def __init__(self) -> None:
        self.meter = UsageMeter()

    def classify(self, incident: Incident) -> ClassificationOutput:
        text = f"{incident.error_message}\n{incident.log_excerpt}".lower()
        scores = {
            cat: sum(1 for pattern in patterns if re.search(pattern, text))
            for cat, patterns in _SIGNALS.items()
        }
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        (best, best_score), (second, second_score) = ranked[0], ranked[1]
        self.meter.record("classify", self.model, 0, 0, 0.0)
        if best_score == 0:
            return ClassificationOutput(
                category=FailureCategory.UNKNOWN, confidence=0.2, rationale="no known failure signature"
            )
        total = sum(scores.values())
        return ClassificationOutput(
            category=best,
            confidence=round(best_score / total, 2),
            rationale=f"matched {best_score} {best.value} signal(s)",
            secondary=second if second_score else None,
        )

    def diagnose(self, incident, category, evidence, runbooks) -> Diagnosis:
        self.meter.record("diagnose", self.model, 0, 0, 0.0)
        findings = [e["finding"] for e in evidence if e.get("finding")]
        root = (
            findings[0] if findings else f"{category.value.replace('_', ' ')} (no confirming tool evidence)"
        )
        return Diagnosis(
            root_cause=root,
            explanation="; ".join(findings) or incident.error_message,
            cited_runbooks=[r["chunk_id"] for r in runbooks[:2]],
            confidence=0.8 if findings else 0.4,
        )

    def propose(self, incident, category, diagnosis, runbooks) -> RemediationProposal:
        self.meter.record("propose", self.model, 0, 0, 0.0)
        steps = [RemediationStep(action=a, owner=o, reversible=r) for a, o, r in _TEMPLATES[category]]
        return RemediationProposal(
            summary=f"Address: {diagnosis.root_cause}",
            steps=steps,
            risk="medium" if any(not s.reversible for s in steps) else "low",
        )


def make_llm(backend: str | None = None) -> TriageLLM:
    backend = backend or os.environ.get(
        "TRIAGE_LLM", "claude" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic"
    )
    if backend == "claude":
        return ClaudeTriageLLM()
    if backend == "heuristic":
        return HeuristicTriageLLM()
    raise ValueError(f"unknown LLM backend {backend!r}")
