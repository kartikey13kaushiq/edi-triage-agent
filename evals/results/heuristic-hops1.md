# EDI triage eval - `heuristic` (max_hops=1, n=32)

_Run 2026-09-24T16:32:59+00:00_

| System | Accuracy |
|---|---|
| Majority-class baseline (`transport_handshake`) | 25.0% |
| Classifier only (no tools) | 68.8% |
| **Agent (classifier + worker evidence)** | **75.0%** |

| Category | n | Classifier recall | Agent recall |
|---|---|---|---|
| certificate_expiry | 7 | 57% | 57% |
| envelope_ack_mismatch | 8 | 75% | 75% |
| partner_misconfig | 7 | 86% | 86% |
| transport_handshake | 8 | 62% | 75% |
| unknown | 2 | 50% | 100% |

- Retrieval: diagnosis cited a runbook from the right failure family in 100% of diagnosed incidents
- Reached the human approval gate: 69%; escalated: 10; crashed runs: 0
- Mean worker hops: 1.00
- Cost per run: $0.0000; latency p50 0.006s, p95 0.008s

## Misses

| Incident | Label | Classifier | Agent | Workers | Note |
|---|---|---|---|---|---|
| INC-002 | transport_handshake | partner_misconfig | unknown | partner_config_worker | no signal in the text; needs the probe |
| INC-006 | transport_handshake | partner_misconfig | unknown | partner_config_worker | text says auth; evidence says host key |
| INC-013 | certificate_expiry | partner_misconfig | unknown | partner_config_worker | text points at partner config; evidence is a future-dated cert |
| INC-014 | certificate_expiry | unknown | unknown | transport_worker | no signal in the text |
| INC-015 | certificate_expiry | partner_misconfig | unknown | partner_config_worker | no signal in the text |
| INC-018 | envelope_ack_mismatch | unknown | unknown | transport_worker |  |
| INC-021 | envelope_ack_mismatch | partner_misconfig | unknown | partner_config_worker | no signal in the text; the ack says rejected |
| INC-028 | partner_misconfig | transport_handshake | unknown | transport_worker | text mentions handshake; evidence is the username |
