"""Generate the labelled incident set (evals/incidents.jsonl).

Every label is consistent with the fixture world in ``src/edi_triage/data/partners.json``:
an incident is labelled with a category only when the partner's recorded state actually
exhibits that failure at the incident time. Several incidents are deliberately misleading
(error text that points at the wrong category, or carries no signal at all) so the eval
measures whether the agent's tool evidence corrects the classifier.

Run: python evals/build_dataset.py
"""

from __future__ import annotations

import json
from pathlib import Path

from edi_triage.x12 import build_functional_ack, build_interchange, build_ta1

OUT = Path(__file__).parent / "incidents.jsonl"
SEPT = "2026-09-15T10:00:00+00:00"  # GLOBEX certs expired, WAYNE signing cert not yet valid
AUG = "2026-08-10T10:00:00+00:00"  # GLOBEX certs still valid
OCT = "2026-10-12T10:00:00+00:00"  # WAYNE certs valid

PO = [["BEG*00*SA*PO-88121**20260915", "PO1*1*10*EA*9.5**VP*SKU-1", "CTT*1"]]


def inc(n, partner, protocol, label, msg, log="", payload="", ack="", at=SEPT, note=""):
    return {
        "label": label,
        "note": note,
        "incident": {
            "incident_id": f"INC-{n:03d}",
            "partner_id": partner,
            "protocol": protocol,
            "error_message": msg,
            "log_excerpt": log,
            "payload_excerpt": payload,
            "ack_excerpt": ack,
            "occurred_at": at,
        },
    }


def build() -> list[dict]:
    ok_acme = build_interchange("PRECISEHUB", "ACMERETAIL", 4411, 4411, PO)
    rows = [
        # ---------------------------------------------------------------- transport
        inc(
            1,
            "UMBRELLA",
            "AS2",
            "transport_handshake",
            "AS2 send failed: javax.net.ssl.SSLHandshakeException: Received fatal alert: protocol_version",
            "POST https://b2b.umbrella.example/receive\nClientHello TLSv1.2 -> alert protocol_version",
        ),
        inc(
            2,
            "UMBRELLA",
            "AS2",
            "transport_handshake",
            "AS2 send failed: no MDN received and message status unknown",
            "POST https://b2b.umbrella.example/receive AS2-To: UMBRELLAPH",
            note="no signal in the text; needs the probe",
        ),
        inc(
            3,
            "HOOLI",
            "SFTP",
            "transport_handshake",
            "SFTP connect failed: Connection timed out after 30000 ms",
            "ssh: connect to host sftp.hooli.example port 22: Connection timed out",
        ),
        inc(
            4,
            "HOOLI",
            "SFTP",
            "transport_handshake",
            "Scheduled outbound delivery to HOOLI missed its window",
            "job hooli-outbound-0915 exceeded runtime; 3 attempts",
            note="no signal in the text",
        ),
        inc(
            5,
            "INITECH",
            "SFTP",
            "transport_handshake",
            "SFTP upload aborted: Host key verification failed",
            "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! sftp.initech.example",
        ),
        inc(
            6,
            "INITECH",
            "SFTP",
            "transport_handshake",
            "SFTP upload failed: authentication-failed before session start",
            "ssh sftp.initech.example: server host key SHA256:Xr4mLw0Q7aZcE2hd not in known_hosts; "
            "user=precise_out",
            note="text says auth; evidence says host key",
        ),
        inc(
            7,
            "UMBRELLA",
            "AS2",
            "transport_handshake",
            "AS2 connection reset by peer during TLS negotiation",
            "POST https://b2b.umbrella.example/receive -> connection reset",
        ),
        inc(
            8,
            "HOOLI",
            "SFTP",
            "transport_handshake",
            "SFTP: No route to host sftp.hooli.example",
            "connect() failed: No route to host",
        ),
        # ------------------------------------------------------------- certificates
        inc(
            9,
            "GLOBEX",
            "AS2",
            "certificate_expiry",
            "AS2 encryption failed: CertificateExpiredException: NotAfter: Tue Sep 01 00:00:00 UTC 2026",
            "POST https://edi.globex.example/as2 AS2-To: GLOBEXLOG",
        ),
        inc(
            10,
            "GLOBEX",
            "AS2",
            "certificate_expiry",
            "MDN received: disposition processed/error: decryption-failed",
            "POST https://edi.globex.example/as2 AS2-To: GLOBEXLOG",
        ),
        inc(
            11,
            "GLOBEX",
            "AS2",
            "certificate_expiry",
            "Partner returned MDN insufficient-message-security",
            "POST https://edi.globex.example/as2 AS2-To: GLOBEXLOG AS2-From: PRECISEHUB",
        ),
        inc(
            12,
            "WAYNE",
            "AS2",
            "certificate_expiry",
            "Signature verification failed: certificate not yet valid",
            "POST https://as2.wayne-dist.example/inbound AS2-To: WAYNEDIST",
        ),
        inc(
            13,
            "WAYNE",
            "AS2",
            "certificate_expiry",
            "MDN: authentication-failed",
            "POST https://as2.wayne-dist.example/inbound AS2-To: WAYNEDIST AS2-From: PRECISEHUB",
            note="text points at partner config; evidence is a future-dated cert",
        ),
        inc(
            14,
            "GLOBEX",
            "AS2",
            "certificate_expiry",
            "Outbound AS2 message to GLOBEX stuck in retry queue",
            "POST https://edi.globex.example/as2 attempt 4/5",
            note="no signal in the text",
        ),
        inc(
            15,
            "WAYNE",
            "AS2",
            "certificate_expiry",
            "AS2 signing step failed for partner WAYNE",
            "POST https://as2.wayne-dist.example/inbound AS2-To: WAYNEDIST",
            note="no signal in the text",
        ),
        # ---------------------------------------------------------------- envelopes
        inc(
            16,
            "ACME",
            "AS2",
            "envelope_ack_mismatch",
            "997 received with AK9=R (rejected) for group 4411",
            "AS2-To: ACMERETAIL",
            build_interchange("PRECISEHUB", "ACMERETAIL", 4411, 4411, PO, se_count_delta=1),
            build_functional_ack("ACMERETAIL", "PRECISEHUB", 9001, 4411, status="R"),
        ),
        inc(
            17,
            "ACME",
            "AS2",
            "envelope_ack_mismatch",
            "TA1 rejection received from ACMERETAIL",
            "AS2-To: ACMERETAIL",
            build_interchange("PRECISEHUB", "ACMERETAIL", 4412, 4412, PO, iea_control=4413),
            build_ta1("ACMERETAIL", "PRECISEHUB", 9002, 4412, code="R", note="001"),
        ),
        inc(
            18,
            "STARK",
            "SFTP",
            "envelope_ack_mismatch",
            "Functional ack could not be correlated to any sent interchange",
            "sftp://files.stark.example:2222/edi/in user=precise_edi",
            build_interchange("PRECISEHUB", "STARKCOMP", 810, 810, PO),
            build_functional_ack("STARKCOMP", "PRECISEHUB", 9003, 999),
        ),
        inc(
            19,
            "STARK",
            "SFTP",
            "envelope_ack_mismatch",
            "Monitoring alert: no functional acknowledgment received within SLA for interchange 000000812",
            "sftp://files.stark.example:2222/edi/in user=precise_edi",
            build_interchange("PRECISEHUB", "STARKCOMP", 812, 812, PO, ge_control=811),
        ),
        inc(
            20,
            "GLOBEX",
            "AS2",
            "envelope_ack_mismatch",
            "997 accepted with errors (AK9=E) from GLOBEXLOG",
            "AS2-To: GLOBEXLOG",
            build_interchange("PRECISEHUB", "GLOBEXLOG", 3301, 3301, PO),
            build_functional_ack("GLOBEXLOG", "PRECISEHUB", 9004, 3301, status="E"),
            at=AUG,
        ),
        inc(
            21,
            "ACME",
            "AS2",
            "envelope_ack_mismatch",
            "Partner reports they cannot process our last file",
            "AS2-To: ACMERETAIL",
            ok_acme,
            build_functional_ack("ACMERETAIL", "PRECISEHUB", 9005, 4411, status="R"),
            note="no signal in the text; the ack says rejected",
        ),
        inc(
            22,
            "STARK",
            "SFTP",
            "envelope_ack_mismatch",
            "AK9 partial acceptance from STARKCOMP",
            "sftp://files.stark.example:2222/edi/in user=precise_edi",
            build_interchange("PRECISEHUB", "STARKCOMP", 815, 815, PO),
            build_functional_ack("STARKCOMP", "PRECISEHUB", 9006, 815, status="P"),
        ),
        inc(
            23,
            "GLOBEX",
            "AS2",
            "envelope_ack_mismatch",
            "Interchange ack mismatch: TA1 refers to 000000501",
            "AS2-To: GLOBEXLOG",
            build_interchange("PRECISEHUB", "GLOBEXLOG", 3302, 3302, PO),
            build_ta1("GLOBEXLOG", "PRECISEHUB", 9007, 501),
            at=AUG,
        ),
        # ------------------------------------------------------- partner configuration
        inc(
            24,
            "STARK",
            "SFTP",
            "partner_misconfig",
            "SFTP login failed: Permission denied (publickey)",
            "sftp://files.stark.example:2222/edi/in user=precise_out",
        ),
        inc(
            25,
            "ACME",
            "AS2",
            "partner_misconfig",
            "MDN disposition: processed/error: unknown-trading-partner",
            "POST https://as2.acme-retail.example/as2/receive AS2-To: ACME-RETAIL AS2-From: PRECISEHUB",
        ),
        inc(
            26,
            "WAYNE",
            "AS2",
            "partner_misconfig",
            "AS2 POST returned HTTP 404 Not Found",
            "POST https://as2.wayne-dist.example/old/inbound AS2-To: WAYNEDIST",
            at=OCT,
        ),
        inc(
            27,
            "GLOBEX",
            "AS2",
            "partner_misconfig",
            "Partner rejected interchange: invalid receiver id in ISA08",
            "AS2-To: GLOBEXLOG",
            build_interchange("PRECISEHUB", "GLOBEX", 3303, 3303, PO),
            at=AUG,
        ),
        inc(
            28,
            "STARK",
            "SFTP",
            "partner_misconfig",
            "SFTP upload failed: handshake completed, authentication failed for user precise_edi2",
            "sftp://files.stark.example:2222/edi/in user=precise_edi2",
            note="text mentions handshake; evidence is the username",
        ),
        inc(
            29,
            "STARK",
            "SFTP",
            "partner_misconfig",
            "SFTP put failed: No such file or directory",
            "sftp://files.stark.example:2222/edi/inbox user=precise_edi",
        ),
        inc(
            30,
            "ACME",
            "AS2",
            "partner_misconfig",
            "Outbound message to ACME bounced",
            "POST https://as2.acme-retail.example/as2/receive AS2-To: ACMERETAIL AS2-From: PRECISE-HUB",
            note="no signal in the text; AS2-From is wrong",
        ),
        # --------------------------------------------------------------- no fault found
        inc(
            31,
            "ACME",
            "AS2",
            "unknown",
            "Operator report: partner says the file looked odd",
            "POST https://as2.acme-retail.example/as2/receive AS2-To: ACMERETAIL",
            ok_acme,
            build_functional_ack("ACMERETAIL", "PRECISEHUB", 9008, 4411),
        ),
        inc(
            32,
            "STARK",
            "SFTP",
            "unknown",
            "Transfer flagged for manual review",
            "sftp://files.stark.example:2222/edi/in user=precise_edi",
        ),
    ]
    return rows


if __name__ == "__main__":
    rows = build()
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"wrote {len(rows)} incidents to {OUT}")
