"""Offline evaluation harness for the triage agent.

Scores three systems on the labelled incident set:

  * majority-class baseline  - always predicts the most frequent label
  * classifier only          - the supervisor's first classification, no tools
  * agent                    - final category after workers' evidence (escalated => "unknown")

and reports per-run cost, latency and hops, so a routing or prompt change is accepted on
evidence rather than impression.

  python evals/run_eval.py                       # heuristic backend, offline, free
  python evals/run_eval.py --llm claude          # native Claude backend (needs ANTHROPIC_API_KEY)
  python evals/run_eval.py --llm openai:gpt-4.1  # any agentkit provider: gemini:..., ollama:..., vllm:...
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from edi_triage.graph import build_graph, initial_state
from edi_triage.llm import make_llm
from edi_triage.models import Incident
from edi_triage.retrieval import default_retriever
from edi_triage.tools import default_toolbox

HERE = Path(__file__).parent


def accuracy(pairs: list[tuple[str, str]]) -> float:
    return sum(p == g for p, g in pairs) / len(pairs)


def evaluate(llm_name: str, max_hops: int, dataset: Path) -> dict:
    rows = [json.loads(line) for line in dataset.read_text().splitlines() if line.strip()]
    labels = [r["label"] for r in rows]
    majority_label, _ = Counter(labels).most_common(1)[0]

    llm = make_llm(llm_name)
    graph = build_graph(llm, default_toolbox(), default_retriever(), InMemorySaver(), max_hops=max_hops)

    records = []
    for row in rows:
        incident = Incident.model_validate(row["incident"])
        cost_before, calls_before = llm.meter.cost_usd, llm.meter.calls
        started = time.perf_counter()
        error = None
        try:
            state = graph.invoke(initial_state(incident), {"configurable": {"thread_id": incident.incident_id}})
        except Exception as exc:  # a crashed run is scored as wrong, not skipped
            state, error = {}, f"{type(exc).__name__}: {exc}"
        latency = time.perf_counter() - started
        cls = state.get("classification", {})
        final = "unknown" if state.get("status") == "escalated" else cls.get("category", "error")
        records.append(
            {
                "incident_id": incident.incident_id,
                "label": row["label"],
                "note": row.get("note", ""),
                "initial": cls.get("initial_category", "error"),
                "final": final,
                "status": state.get("status", "error"),
                "hops": len(state.get("visited_workers", [])),
                "workers": state.get("visited_workers", []),
                "cited": state.get("diagnosis", {}).get("cited_runbooks", []),
                "llm_calls": llm.meter.calls - calls_before,
                "cost_usd": llm.meter.cost_usd - cost_before,
                "latency_s": latency,
                "error": error,
            }
        )

    per_category = {}
    for cat in sorted(set(labels)):
        subset = [r for r in records if r["label"] == cat]
        per_category[cat] = {
            "n": len(subset),
            "classifier_recall": accuracy([(r["initial"], r["label"]) for r in subset]),
            "agent_recall": accuracy([(r["final"], r["label"]) for r in subset]),
        }
    # Retrieval check: did the diagnosis cite a runbook for the right failure family?
    family = {
        "transport_handshake": ("as2-transport", "sftp-transport"),
        "certificate_expiry": ("certificates",),
        "envelope_ack_mismatch": ("envelope-ack",),
        "partner_misconfig": ("partner-config",),
    }
    diagnosed = [r for r in records if r["label"] in family and r["cited"]]
    latencies = sorted(r["latency_s"] for r in records)
    return {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "llm": llm_name,
        "model": getattr(llm, "model", llm_name),
        "max_hops": max_hops,
        "n": len(records),
        "accuracy": {
            "majority_baseline": accuracy([(majority_label, g) for g in labels]),
            "classifier_only": accuracy([(r["initial"], r["label"]) for r in records]),
            "agent": accuracy([(r["final"], r["label"]) for r in records]),
        },
        "majority_label": majority_label,
        "per_category": per_category,
        "retrieval_family_hit_rate": (
            sum(any(c.startswith(family[r["label"]]) for c in r["cited"]) for r in diagnosed) / len(diagnosed)
            if diagnosed
            else None
        ),
        "reached_approval_gate": sum(r["status"] == "awaiting_approval" for r in records) / len(records),
        "escalated": sum(r["status"] == "escalated" for r in records),
        "errors": sum(1 for r in records if r["error"]),
        "mean_hops": statistics.mean(r["hops"] for r in records),
        "cost_per_run_usd": sum(r["cost_usd"] for r in records) / len(records),
        "latency_p50_s": latencies[len(latencies) // 2],
        "latency_p95_s": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))],
        "records": records,
    }


def to_markdown(report: dict) -> str:
    acc = report["accuracy"]
    lines = [
        f"# EDI triage eval - `{report['model']}` (max_hops={report['max_hops']}, n={report['n']})",
        "",
        f"_Run {report['run_at']}_",
        "",
        "| System | Accuracy |",
        "|---|---|",
        f"| Majority-class baseline (`{report['majority_label']}`) | {acc['majority_baseline']:.1%} |",
        f"| Classifier only (no tools) | {acc['classifier_only']:.1%} |",
        f"| **Agent (classifier + worker evidence)** | **{acc['agent']:.1%}** |",
        "",
        "| Category | n | Classifier recall | Agent recall |",
        "|---|---|---|---|",
    ]
    for cat, m in report["per_category"].items():
        lines.append(f"| {cat} | {m['n']} | {m['classifier_recall']:.0%} | {m['agent_recall']:.0%} |")
    hit = report["retrieval_family_hit_rate"]
    lines += [
        "",
        f"- Retrieval: diagnosis cited a runbook from the right failure family in {hit:.0%} of diagnosed incidents"
        if hit is not None
        else "- Retrieval: n/a",
        f"- Reached the human approval gate: {report['reached_approval_gate']:.0%}; "
        f"escalated: {report['escalated']}; crashed runs: {report['errors']}",
        f"- Mean worker hops: {report['mean_hops']:.2f}",
        f"- Cost per run: ${report['cost_per_run_usd']:.4f}; "
        f"latency p50 {report['latency_p50_s']:.3f}s, p95 {report['latency_p95_s']:.3f}s",
        "",
        "## Misses",
        "",
        "| Incident | Label | Classifier | Agent | Workers | Note |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["records"]:
        if r["final"] != r["label"]:
            lines.append(
                f"| {r['incident_id']} | {r['label']} | {r['initial']} | {r['final']} | "
                f"{' > '.join(r['workers'])} | {r['note'] or r['error'] or ''} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", default="heuristic", help="heuristic | claude | provider:model (any agentkit spec)")
    parser.add_argument("--max-hops", type=int, default=4)
    parser.add_argument("--dataset", type=Path, default=HERE / "incidents.jsonl")
    parser.add_argument("--out", type=Path, default=HERE / "results")
    args = parser.parse_args()

    report = evaluate(args.llm, args.max_hops, args.dataset)
    args.out.mkdir(exist_ok=True)
    stem = f"{args.llm.replace(':', '_').replace('/', '_')}-hops{args.max_hops}"
    (args.out / f"{stem}.json").write_text(json.dumps(report, indent=2))
    (args.out / f"{stem}.md").write_text(to_markdown(report))
    print(to_markdown(report))


if __name__ == "__main__":
    main()
