"""Minimal, dependency-free X12 envelope parser used by the envelope worker.

It understands the ISA/IEA, GS/GE and ST/SE envelopes plus the 997/999 (AK1/AK9)
and TA1 acknowledgements - enough to detect control-number and ack mismatches,
which is what triage needs. It is deliberately not a full X12 validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Envelope:
    element_sep: str
    segment_term: str
    sender_id: str = ""
    receiver_id: str = ""
    isa_control: str = ""
    iea_control: str = ""
    iea_group_count: int | None = None
    groups: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "isa_control": self.isa_control,
            "iea_control": self.iea_control,
            "groups": self.groups,
            "issues": self.issues,
        }


class X12ParseError(ValueError):
    pass


def _segments(raw: str) -> tuple[list[list[str]], str, str]:
    raw = raw.strip()
    if not raw.startswith("ISA") or len(raw) < 106:
        raise X12ParseError("payload does not start with a complete 106-character ISA segment")
    element_sep = raw[3]
    segment_term = raw[105]
    segments = [seg.strip().split(element_sep) for seg in raw.split(segment_term) if seg.strip()]
    return segments, element_sep, segment_term


def parse_envelope(raw: str) -> Envelope:
    segments, element_sep, segment_term = _segments(raw)
    env = Envelope(element_sep=element_sep, segment_term=segment_term)
    group: dict | None = None
    txn: dict | None = None

    for seg in segments:
        tag = seg[0]
        if tag == "ISA":
            env.sender_id = seg[6].strip()
            env.receiver_id = seg[8].strip()
            env.isa_control = seg[13].strip()
        elif tag == "GS":
            group = {"control": seg[6].strip(), "ge_control": None, "ge_count": None, "transactions": []}
            env.groups.append(group)
        elif tag == "ST":
            txn = {
                "control": seg[2].strip(),
                "set_id": seg[1].strip(),
                "se_control": None,
                "declared_segments": None,
                "counted_segments": 1,
            }
            if group is not None:
                group["transactions"].append(txn)
        elif tag == "SE":
            if txn is not None:
                txn["counted_segments"] += 1
                txn["declared_segments"] = int(seg[1])
                txn["se_control"] = seg[2].strip()
                txn = None
        elif tag == "GE":
            if group is not None:
                group["ge_count"] = int(seg[1])
                group["ge_control"] = seg[2].strip()
                group = None
        elif tag == "IEA":
            env.iea_group_count = int(seg[1])
            env.iea_control = seg[2].strip()
        elif txn is not None:
            txn["counted_segments"] += 1

    _validate(env)
    return env


def _validate(env: Envelope) -> None:
    if env.iea_control and env.isa_control != env.iea_control:
        env.issues.append(f"ISA13 {env.isa_control} != IEA02 {env.iea_control}")
    if not env.iea_control:
        env.issues.append("missing IEA trailer")
    if env.iea_group_count is not None and env.iea_group_count != len(env.groups):
        env.issues.append(f"IEA01 declares {env.iea_group_count} groups, found {len(env.groups)}")
    for g in env.groups:
        if g["ge_control"] is None:
            env.issues.append(f"group {g['control']} missing GE trailer")
            continue
        if g["ge_control"] != g["control"]:
            env.issues.append(f"GS06 {g['control']} != GE02 {g['ge_control']}")
        if g["ge_count"] != len(g["transactions"]):
            env.issues.append(
                f"GE01 declares {g['ge_count']} transactions in group {g['control']}, "
                f"found {len(g['transactions'])}"
            )
        for t in g["transactions"]:
            if t["se_control"] is None:
                env.issues.append(f"transaction {t['control']} missing SE trailer")
                continue
            if t["se_control"] != t["control"]:
                env.issues.append(f"ST02 {t['control']} != SE02 {t['se_control']}")
            if t["declared_segments"] != t["counted_segments"]:
                env.issues.append(
                    f"SE01 declares {t['declared_segments']} segments in {t['control']}, "
                    f"counted {t['counted_segments']}"
                )


# 997/999 AK9 and TA1 codes that mean the partner did not accept the data.
_REJECT_CODES = {
    "R": "rejected",
    "E": "accepted with errors",
    "P": "partially accepted",
    "M": "rejected (MAC failure)",
}


def compare_ack(payload_raw: str, ack_raw: str) -> dict:
    """Compare a sent interchange with the functional/interchange ack the partner returned."""
    sent = parse_envelope(payload_raw)
    ack_segments, _, _ = _segments(ack_raw)
    result: dict = {"ack_type": None, "matched": True, "status": "accepted", "issues": []}

    for seg in ack_segments:
        tag = seg[0]
        if tag == "TA1":
            result["ack_type"] = "TA1"
            if seg[1].strip() != sent.isa_control:
                result["matched"] = False
                result["issues"].append(
                    f"TA1 references interchange {seg[1].strip()}, sent {sent.isa_control}"
                )
            if seg[4].strip() != "A":
                result["status"] = "rejected"
                result["issues"].append(f"TA1 ack code {seg[4].strip()}, note code {seg[5].strip()}")
        elif tag == "ST" and seg[1].strip() in {"997", "999"}:
            result["ack_type"] = seg[1].strip()
        elif tag == "AK1":
            sent_groups = {g["control"] for g in sent.groups}
            if seg[2].strip() not in sent_groups:
                result["matched"] = False
                result["issues"].append(
                    f"AK1 references group {seg[2].strip()}, sent groups {sorted(sent_groups)}"
                )
        elif tag == "AK9":
            code = seg[1].strip()
            if code in _REJECT_CODES:
                result["status"] = _REJECT_CODES[code]
                result["issues"].append(f"AK9 status {code} ({_REJECT_CODES[code]})")
        elif tag in {"AK5", "IK5"} and seg[1].strip() in _REJECT_CODES:
            result["issues"].append(f"{tag} transaction status {seg[1].strip()} codes {seg[2:]}")
    return result


def build_interchange(
    sender: str,
    receiver: str,
    isa_control: int,
    group_control: int,
    transactions: list[list[str]],
    *,
    set_id: str = "850",
    iea_control: int | None = None,
    ge_control: int | None = None,
    se_count_delta: int = 0,
    date: str = "260915",
    time: str = "1200",
) -> str:
    """Build a well-formed X12 5010 interchange (or a deliberately broken one via the overrides).

    ``transactions`` holds the body segments of each transaction set, without ST/SE.
    """
    isa = (
        f"ISA*00*{'':10}*00*{'':10}*ZZ*{sender:<15}*ZZ*{receiver:<15}*{date}*{time}*^*00501*"
        f"{isa_control:09d}*0*P*:~"
    )
    assert len(isa) == 106, len(isa)
    segs = [isa, f"GS*PO*{sender}*{receiver}*20{date}*{time}*{group_control}*X*005010~"]
    for n, body in enumerate(transactions, start=1):
        st_control = f"{n:04d}"
        segs.append(f"ST*{set_id}*{st_control}~")
        segs.extend(f"{b}~" for b in body)
        segs.append(f"SE*{len(body) + 2 + se_count_delta}*{st_control}~")
    segs.append(f"GE*{len(transactions)}*{ge_control if ge_control is not None else group_control}~")
    segs.append(f"IEA*1*{(iea_control if iea_control is not None else isa_control):09d}~")
    return "".join(segs)


def build_functional_ack(
    sender: str, receiver: str, isa_control: int, acked_group: int, status: str = "A", ack_type: str = "997"
) -> str:
    """A 997/999 from ``sender`` acknowledging group ``acked_group`` with AK9 ``status``."""
    body = [f"AK1*PO*{acked_group}", f"AK9*{status}*1*1*{1 if status == 'A' else 0}"]
    return build_interchange(sender, receiver, isa_control, 1, [body], set_id=ack_type)


def build_ta1(
    sender: str, receiver: str, isa_control: int, acked_isa: int, code: str = "A", note: str = "000"
) -> str:
    isa = (
        f"ISA*00*{'':10}*00*{'':10}*ZZ*{sender:<15}*ZZ*{receiver:<15}*260915*1200*^*00501*"
        f"{isa_control:09d}*0*P*:~"
    )
    return isa + f"TA1*{acked_isa:09d}*260915*1200*{code}*{note}~IEA*0*{isa_control:09d}~"
