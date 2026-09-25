from edi_triage import x12

PO = [["BEG*00*SA*PO-1**20260915", "PO1*1*10*EA*9.5**VP*SKU-1", "CTT*1"]]


def test_well_formed_interchange_has_no_issues():
    env = x12.parse_envelope(x12.build_interchange("US", "THEM", 42, 7, PO))
    assert env.issues == []
    assert env.isa_control == "000000042"
    assert env.receiver_id == "THEM"
    assert env.groups[0]["transactions"][0]["counted_segments"] == 5


def test_trailer_mismatches_are_reported():
    raw = x12.build_interchange("US", "THEM", 42, 7, PO, iea_control=43, ge_control=8, se_count_delta=1)
    issues = x12.parse_envelope(raw).issues
    assert any("ISA13" in i for i in issues)
    assert any("GS06 7 != GE02 8" in i for i in issues)
    assert any("SE01 declares 6" in i for i in issues)


def test_rejected_functional_ack():
    sent = x12.build_interchange("US", "THEM", 42, 7, PO)
    ack = x12.build_functional_ack("THEM", "US", 900, 7, status="R")
    result = x12.compare_ack(sent, ack)
    assert result["ack_type"] == "997"
    assert result["matched"] is True
    assert result["status"] == "rejected"


def test_ack_for_unknown_group_is_uncorrelated():
    sent = x12.build_interchange("US", "THEM", 42, 7, PO)
    result = x12.compare_ack(sent, x12.build_functional_ack("THEM", "US", 900, 99))
    assert result["matched"] is False


def test_ta1_referencing_other_interchange():
    sent = x12.build_interchange("US", "THEM", 42, 7, PO)
    result = x12.compare_ack(sent, x12.build_ta1("THEM", "US", 900, 41))
    assert result["ack_type"] == "TA1"
    assert result["matched"] is False
