"""Integration tests against a real Postgres with pgvector. Skipped unless TEST_DATABASE_URL is set.

TEST_DATABASE_URL=postgresql://postgres@localhost:5432/triage pytest tests/test_postgres.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from edi_triage.checkpoint import make_checkpointer
from edi_triage.llm import HeuristicTriageLLM
from edi_triage.models import ApprovalDecision
from edi_triage.retrieval import PgVectorRetriever, load_runbook_chunks
from edi_triage.service import TriageService

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")
ROOT = Path(__file__).parent.parent


def test_pgvector_index_and_search():
    retriever = PgVectorRetriever(URL)
    assert retriever.index(load_runbook_chunks()) > 10
    top = retriever.search("SSH host key changed fingerprint differs known_hosts", k=1)[0]
    assert top.chunk.source == "sftp-transport.md"
    assert 0 < top.score <= 1


def test_suspended_run_survives_process_exit(labelled):
    """Start triage in a child process that exits at the approval gate; resume it here."""
    incident = json.dumps(labelled["INC-005"]["incident"])
    script = (
        "import json, sys\n"
        "from edi_triage.llm import HeuristicTriageLLM\n"
        "from edi_triage.models import Incident\n"
        "from edi_triage.service import TriageService\n"
        "view = TriageService(llm=HeuristicTriageLLM()).start(Incident.model_validate_json(sys.argv[1]))\n"
        "print(json.dumps({'thread_id': view['thread_id'], 'status': view['status']}))\n"
    )
    env = {**os.environ, "DATABASE_URL": URL, "PYTHONPATH": str(ROOT / "src")}
    out = subprocess.run([sys.executable, "-c", script, incident], env=env, capture_output=True, text=True, check=True)
    started = json.loads(out.stdout.strip().splitlines()[-1])
    assert started["status"] == "awaiting_approval"

    service = TriageService(llm=HeuristicTriageLLM(), checkpointer=make_checkpointer(URL))
    assert service.get(started["thread_id"])["pending_approval"] is not None
    final = service.decide(started["thread_id"], ApprovalDecision(approved=True, reviewer="ops"))
    assert final["status"] == "approved"
    assert final["classification"]["category"] == "transport_handshake"
