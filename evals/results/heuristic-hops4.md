# EDI triage eval - `heuristic` (max_hops=4, n=32)

_Run 2026-09-24T16:42:01+00:00_

| System | Accuracy |
|---|---|
| Majority-class baseline (`transport_handshake`) | 25.0% |
| Classifier only (no tools) | 68.8% |
| **Agent (classifier + worker evidence)** | **100.0%** |

| Category | n | Classifier recall | Agent recall |
|---|---|---|---|
| certificate_expiry | 7 | 57% | 100% |
| envelope_ack_mismatch | 8 | 75% | 100% |
| partner_misconfig | 7 | 86% | 100% |
| transport_handshake | 8 | 62% | 100% |
| unknown | 2 | 50% | 100% |

- Retrieval: diagnosis cited a runbook from the right failure family in 100% of diagnosed incidents
- Reached the human approval gate: 94%; escalated: 2; crashed runs: 0
- Mean worker hops: 1.66
- Cost per run: $0.0000; latency p50 0.006s, p95 0.010s

## Misses

| Incident | Label | Classifier | Agent | Workers | Note |
|---|---|---|---|---|---|
