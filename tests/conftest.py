import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from edi_triage.llm import HeuristicTriageLLM
from edi_triage.models import Incident
from edi_triage.service import TriageService

DATASET = Path(__file__).parent.parent / "evals" / "incidents.jsonl"


@pytest.fixture(scope="session")
def labelled() -> dict[str, dict]:
    rows = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    return {r["incident"]["incident_id"]: r for r in rows}


@pytest.fixture
def incident(labelled):
    def get(incident_id: str) -> Incident:
        return Incident.model_validate(labelled[incident_id]["incident"])

    return get


@pytest.fixture
def service() -> TriageService:
    return TriageService(llm=HeuristicTriageLLM(), checkpointer=InMemorySaver())
