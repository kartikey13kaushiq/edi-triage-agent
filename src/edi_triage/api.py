"""FastAPI surface for the triage agent.

POST /incidents                   start triage; returns at the approval gate
GET  /incidents/{thread_id}       current state (classification, evidence, proposal, status)
POST /incidents/{thread_id}/decision   approve or reject the proposed remediation
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException

from .models import ApprovalDecision, Incident
from .service import TriageService

app = FastAPI(title="EDI Failure Triage Agent", version="0.1.0")


@lru_cache(maxsize=1)
def get_service() -> TriageService:
    return TriageService()


Service = Annotated[TriageService, Depends(get_service)]


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/incidents", status_code=201)
def create_incident(incident: Incident, service: Service) -> dict:
    return service.start(incident)


@app.get("/incidents/{thread_id}")
def get_incident(thread_id: str, service: Service) -> dict:
    try:
        return service.get(thread_id)
    except KeyError:
        raise HTTPException(404, f"unknown thread {thread_id}") from None


@app.post("/incidents/{thread_id}/decision")
def decide(thread_id: str, decision: ApprovalDecision, service: Service) -> dict:
    try:
        return service.decide(thread_id, decision)
    except KeyError:
        raise HTTPException(404, f"unknown thread {thread_id}") from None
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from exc
