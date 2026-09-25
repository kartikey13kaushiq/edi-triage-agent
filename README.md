# B2B/EDI Failure Triage Agent

A supervisor-worker **LangGraph** agent that classifies and diagnoses failed B2B/EDI transfers
(AS2/SFTP handshake errors, X12 envelope and 997/999/TA1 ack mismatches, certificate expiry,
partner misconfiguration). It routes each incident to a specialised worker, grounds the diagnosis
in a **pgvector**-indexed runbook corpus, and **stops at a human approval gate** before any
remediation is acted on. The agent never writes to production.

```mermaid
flowchart LR
    S([incident]) --> SUP{supervisor}
    SUP -- classify + route --> T[transport_worker]
    SUP --> C[certificate_worker]
    SUP --> P[partner_config_worker]
    SUP --> E[envelope_worker]
    T & C & P & E -- evidence --> SUP
    SUP -- confirmed --> D[diagnose<br/>RAG over runbooks]
    SUP -- hop budget spent --> X([escalate to L3])
    D --> PR[propose remediation]
    PR --> H{{human_approval<br/>interrupt}}
    H -- approve / reject --> F([end])
```

## What it does

| Concern | How it is handled |
|---|---|
| **Orchestration** | `StateGraph` with a supervisor that classifies once and routes with `Command(goto=...)`. Workers report back to the supervisor, and a clean worker triggers a reroute to the next most likely category. That return path is the cycle, and `max_hops` bounds it. |
| **Evidence over guesses** | The worker that finds confirming evidence decides the final category, so a misleading error message gets corrected. Example: `authentication-failed` looks like partner config, but the certificate worker finds a not-yet-valid certificate. |
| **Typed tool contracts** | Every tool (`src/edi_triage/tools.py`) takes typed args and returns a `ToolResult` with a structured `ToolError{code, message, retryable}`. Tools never raise into the graph. They are all read-only: probe, certificate check, X12 parse, ack correlation, identity check. |
| **Real X12 logic** | `x12.py` parses ISA/GS/ST envelopes. It checks ISA13/IEA02, GS06/GE02, ST02/SE02 and SE01 segment counts, and correlates 997/999 AK1/AK9 and TA1 acks against what was sent. |
| **RAG** | Runbooks are chunked per `##` section, embedded, and searched by cosine similarity in pgvector (HNSW index) or in memory. The diagnosis must cite chunk ids. |
| **Human in the loop** | `human_approval` calls `interrupt()`. The run is checkpointed and suspended until `POST /incidents/{id}/decision` resumes it with `Command(resume=...)`. |
| **Durability** | `PostgresSaver` checkpoints every super-step, so a suspended run survives a process restart. `tests/test_postgres.py` starts a run in a child process, lets it exit, and resumes it from another process. |
| **Observability** | Langfuse (when keys are set) gets one `agent` trace per run, a span per node, a `tool` span per tool call (errors at WARNING), and a `generation` per model call with token usage and cost. |
| **Structured LLM I/O** | Claude calls use `client.messages.parse(output_format=PydanticModel)`, so classify, diagnose and propose each return a validated object. A refusal or empty parse raises instead of slipping through. |

## Evaluation

`evals/incidents.jsonl` holds 32 labelled incidents built by `evals/build_dataset.py`. Labels
are consistent with the recorded partner state in `data/partners.json`. Nine incidents are
deliberately misleading or carry no signal in the error text. The harness scores a
**majority-class baseline**, the **classifier alone**, and the **full agent**, plus retrieval hit
rate, cost per run, latency and hop count.

Offline run (deterministic heuristic backend, no API key, `python evals/run_eval.py`):

| System | Accuracy (n=32) |
|---|---|
| Majority-class baseline | 25.0% |
| Classifier only (no tools) | 68.8% |
| Agent, `max_hops=1` | 75.0% |
| **Agent, `max_hops=4`** | **100.0%** |

Read these numbers for what they are. On this synthetic set the tools can always find the
ground truth, so 100% shows that the routing and reroute logic is correct. It does not say how
well a model classifies. The useful signal is the gap: tool evidence adds 31 points over the
classifier, and limiting the hop budget to 1 gives back most of that gain. Full reports are in
`evals/results/`. To score the Claude backend on the same set:

```bash
export ANTHROPIC_API_KEY=...
python evals/run_eval.py --llm claude        # writes evals/results/claude-hops4.{json,md}
```

## Run it

```bash
pip install -e ".[dev,postgres,api,observability]"

# offline: heuristic backend, in-memory checkpointer and retriever
TRIAGE_LLM=heuristic edi-triage run examples/globex-cert.json --decision approve

# with Claude + durable checkpoints + pgvector
export ANTHROPIC_API_KEY=... DATABASE_URL=postgresql://postgres:postgres@localhost:5432/triage
psql "$DATABASE_URL" -f sql/schema.sql && edi-triage ingest
RETRIEVER=pgvector uvicorn edi_triage.api:app
```

```bash
curl -s localhost:8000/incidents -H 'content-type: application/json' -d @examples/globex-cert.json
# -> {"thread_id": "INC-...", "status": "awaiting_approval", "pending_approval": {...}}
curl -s localhost:8000/incidents/<thread_id>/decision -H 'content-type: application/json' \
     -d '{"approved": true, "reviewer": "oncall"}'
```

Or from the repo root: `docker compose up --build` (Postgres with pgvector, and this API on :8001).

Configuration: `TRIAGE_LLM` (`claude` | `heuristic`), `TRIAGE_MODEL` (default `claude-opus-5`),
`DATABASE_URL`, `RETRIEVER=pgvector`, `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST`.

## Tests

```bash
pytest -q                                                    # 22 tests, fully offline
TEST_DATABASE_URL=postgresql://... pytest tests/test_postgres.py   # pgvector + cross-process resume
```

## Layout

```
src/edi_triage/
  graph.py          supervisor, workers, diagnose, propose, human_approval, escalate
  tools.py          read-only typed tool surface with structured errors
  x12.py            X12 envelope parser, ack correlation, test-data builders
  retrieval.py      chunking, hashing embedder, in-memory and pgvector retrievers
  llm.py            Claude backend (structured outputs, cost metering) and heuristic backend
  checkpoint.py     PostgresSaver / InMemorySaver
  observability.py  Langfuse tracer with no-op fallback
  service.py api.py cli.py
  data/             partner directory fixture and runbook corpus
evals/              dataset builder, labelled incidents, harness, results
sql/schema.sql      pgvector table, HNSW index, least-privilege agent role
```
