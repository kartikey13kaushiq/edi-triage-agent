"""Command line entry point: ``edi-triage run|ingest``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .models import ApprovalDecision, Incident
from .retrieval import PgVectorRetriever, load_runbook_chunks
from .service import TriageService


def _run(args: argparse.Namespace) -> int:
    incident = Incident.model_validate_json(Path(args.incident).read_text())
    service = TriageService()
    view = service.start(incident)
    print(
        json.dumps(
            {k: view.get(k) for k in ("thread_id", "status", "classification", "diagnosis", "proposal")},
            indent=2,
            default=str,
        )
    )
    if view["status"] != "awaiting_approval":
        return 0
    if args.decision == "ask":
        answer = input("\nApprove the proposed remediation? [y/N] ").strip().lower()
        approved = answer in {"y", "yes"}
    else:
        approved = args.decision == "approve"
    final = service.decide(
        view["thread_id"],
        ApprovalDecision(approved=approved, reviewer=os.environ.get("USER", "cli"), comment="via CLI"),
    )
    print(f"\nstatus: {final['status']}")
    return 0


def _ingest(args: argparse.Namespace) -> int:
    dsn = args.database_url or os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required to index runbooks into pgvector", file=sys.stderr)
        return 2
    count = PgVectorRetriever(dsn).index(load_runbook_chunks())
    print(f"indexed {count} runbook chunks")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edi-triage")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="triage one incident JSON file")
    run.add_argument("incident")
    run.add_argument("--decision", choices=["ask", "approve", "reject"], default="ask")
    run.set_defaults(func=_run)
    ingest = sub.add_parser("ingest", help="index runbooks into pgvector")
    ingest.add_argument("--database-url")
    ingest.set_defaults(func=_ingest)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
