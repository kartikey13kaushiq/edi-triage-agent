"""Contract-first, read-only tool surface for the diagnosis workers.

Every tool:
  * takes typed arguments and returns a ``ToolResult`` (never raises to the graph),
  * reports failures as a structured ``ToolError`` with a ``retryable`` flag,
  * is read-only: none of them can change partner configuration or resend data.

The partner directory and connectivity probe are pluggable. The defaults are backed
by ``data/partners.json`` so the agent, tests and evals run fully offline.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from . import x12
from .observability import get_tracer

DATA_DIR = Path(__file__).parent / "data"


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None

    @classmethod
    def success(cls, tool: str, data: dict[str, Any]) -> "ToolResult":
        return cls(tool=tool, ok=True, data=data)

    @classmethod
    def failure(cls, tool: str, code: str, message: str, retryable: bool = False) -> "ToolResult":
        return cls(tool=tool, ok=False, error=ToolError(code=code, message=message, retryable=retryable))


def traced_tool(method: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
    """Record each tool call (arguments, result, structured error) as a Langfuse tool span."""

    @functools.wraps(method)
    def wrapper(self: "Toolbox", *args: Any, **kwargs: Any) -> ToolResult:
        with get_tracer().observe(
            method.__name__, as_type="tool", input={"args": args, "kwargs": kwargs}
        ) as obs:
            result = method(self, *args, **kwargs)
            obs.update(
                output=result.model_dump(mode="json"),
                level="DEFAULT" if result.ok else "WARNING",
            )
            return result

    return wrapper


class PartnerDirectory(Protocol):
    def get(self, partner_id: str) -> dict[str, Any] | None: ...


class ConnectivityProbe(Protocol):
    def probe(self, partner: dict[str, Any]) -> dict[str, Any]: ...


class JsonPartnerDirectory:
    def __init__(self, path: Path = DATA_DIR / "partners.json"):
        self._partners = {p["partner_id"]: p for p in json.loads(path.read_text())}

    def get(self, partner_id: str) -> dict[str, Any] | None:
        return self._partners.get(partner_id)


class RecordedProbe:
    """Returns the last recorded probe for a partner (the monitoring system's view).

    A live implementation would open a TCP/TLS or SSH connection from a sandboxed,
    egress-restricted runner and report what it saw; it must still never send data.
    """

    def probe(self, partner: dict[str, Any]) -> dict[str, Any]:
        return partner.get("last_probe", {"reachable": None, "detail": "no probe recorded"})


@dataclass
class Toolbox:
    partners: PartnerDirectory
    probe: ConnectivityProbe
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    # -- tools -----------------------------------------------------------------

    @traced_tool
    def get_partner_profile(self, partner_id: str) -> ToolResult:
        partner = self.partners.get(partner_id)
        if partner is None:
            return ToolResult.failure(
                "get_partner_profile", "PARTNER_NOT_FOUND", f"no partner {partner_id!r}"
            )
        public = {k: v for k, v in partner.items() if k not in {"last_probe", "certificates"}}
        return ToolResult.success("get_partner_profile", public)

    @traced_tool
    def check_certificates(
        self, partner_id: str, as_of: datetime | None = None, warn_days: int = 14
    ) -> ToolResult:
        """Certificate validity at ``as_of`` (the incident time), defaulting to now."""
        partner = self.partners.get(partner_id)
        if partner is None:
            return ToolResult.failure("check_certificates", "PARTNER_NOT_FOUND", f"no partner {partner_id!r}")
        now = as_of or self.clock()
        certs = []
        for cert in partner.get("certificates", []):
            not_before = datetime.fromisoformat(cert["not_before"])
            not_after = datetime.fromisoformat(cert["not_after"])
            days_left = (not_after - now).days
            if now < not_before:
                status = "not_yet_valid"
            elif now > not_after:
                status = "expired"
            elif days_left <= warn_days:
                status = "expiring_soon"
            else:
                status = "valid"
            certs.append({**cert, "days_remaining": days_left, "status": status})
        problems = [c for c in certs if c["status"] in {"expired", "not_yet_valid"}]
        return ToolResult.success(
            "check_certificates", {"certificates": certs, "has_invalid_certificate": bool(problems)}
        )

    @traced_tool
    def probe_endpoint(self, partner_id: str) -> ToolResult:
        partner = self.partners.get(partner_id)
        if partner is None:
            return ToolResult.failure("probe_endpoint", "PARTNER_NOT_FOUND", f"no partner {partner_id!r}")
        try:
            return ToolResult.success("probe_endpoint", self.probe.probe(partner))
        except TimeoutError as exc:
            return ToolResult.failure("probe_endpoint", "PROBE_TIMEOUT", str(exc), retryable=True)

    @traced_tool
    def parse_x12_envelope(self, raw: str) -> ToolResult:
        if not raw.strip():
            return ToolResult.failure("parse_x12_envelope", "NO_PAYLOAD", "incident has no payload excerpt")
        try:
            return ToolResult.success("parse_x12_envelope", x12.parse_envelope(raw).to_dict())
        except (x12.X12ParseError, IndexError, ValueError) as exc:
            return ToolResult.failure("parse_x12_envelope", "UNPARSEABLE_X12", str(exc))

    @traced_tool
    def compare_acknowledgement(self, payload_raw: str, ack_raw: str) -> ToolResult:
        if not payload_raw.strip() or not ack_raw.strip():
            return ToolResult.failure(
                "compare_acknowledgement", "MISSING_INPUT", "need both the sent payload and the received ack"
            )
        try:
            return ToolResult.success("compare_acknowledgement", x12.compare_ack(payload_raw, ack_raw))
        except (x12.X12ParseError, IndexError, ValueError) as exc:
            return ToolResult.failure("compare_acknowledgement", "UNPARSEABLE_X12", str(exc))

    @traced_tool
    def check_identity_config(self, partner_id: str, log_excerpt: str, payload_raw: str) -> ToolResult:
        """Compare identifiers seen on the wire against the partner profile."""
        partner = self.partners.get(partner_id)
        if partner is None:
            return ToolResult.failure(
                "check_identity_config", "PARTNER_NOT_FOUND", f"no partner {partner_id!r}"
            )
        mismatches: list[str] = []

        headers = {
            "AS2-To": partner.get("as2_id_theirs"),
            "AS2-From": partner.get("as2_id_ours"),
        }
        for header, expected in headers.items():
            m = re.search(rf"{header}:\s*\"?([^\s\"]+)", log_excerpt)
            if m and expected and m.group(1) != expected:
                mismatches.append(f"{header} on the wire is {m.group(1)!r}, profile expects {expected!r}")

        url = re.search(r"(https?://\S+|sftp://\S+)", log_excerpt)
        if url and partner.get("endpoint_url") and url.group(1).rstrip(".,") != partner["endpoint_url"]:
            mismatches.append(
                f"connected to {url.group(1)!r}, profile endpoint is {partner['endpoint_url']!r}"
            )

        user = re.search(r"user(?:name)?[=: ]+'?([\w.-]+)", log_excerpt, re.IGNORECASE)
        if user and partner.get("sftp_user") and user.group(1) != partner["sftp_user"]:
            mismatches.append(f"authenticated as {user.group(1)!r}, profile user is {partner['sftp_user']!r}")

        if payload_raw.strip():
            try:
                env = x12.parse_envelope(payload_raw)
                expected_rx = partner.get("isa_receiver_id")
                if expected_rx and env.receiver_id != expected_rx:
                    mismatches.append(
                        f"ISA08 receiver is {env.receiver_id!r}, partner expects {expected_rx!r}"
                    )
            except (x12.X12ParseError, IndexError, ValueError):
                pass

        return ToolResult.success("check_identity_config", {"mismatches": mismatches})


def default_toolbox() -> Toolbox:
    return Toolbox(partners=JsonPartnerDirectory(), probe=RecordedProbe())
