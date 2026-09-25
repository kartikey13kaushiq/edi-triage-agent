from datetime import datetime, timezone

from edi_triage.tools import default_toolbox

tools = default_toolbox()


def status_by_usage(partner, when):
    result = tools.check_certificates(partner, as_of=when)
    return {c["usage"]: c["status"] for c in result.data["certificates"]}


def test_certificate_status_is_evaluated_at_incident_time():
    assert status_by_usage("GLOBEX", datetime(2026, 8, 10, tzinfo=timezone.utc)) == {
        "signing": "valid",
        "encryption": "valid",
    }
    assert status_by_usage("GLOBEX", datetime(2026, 9, 15, tzinfo=timezone.utc))["signing"] == "expired"
    assert status_by_usage("WAYNE", datetime(2026, 9, 15, tzinfo=timezone.utc))["signing"] == "not_yet_valid"


def test_unknown_partner_is_a_structured_error_not_an_exception():
    result = tools.check_certificates("NOPE")
    assert not result.ok
    assert result.error.code == "PARTNER_NOT_FOUND"
    assert result.error.retryable is False


def test_identity_mismatches():
    result = tools.check_identity_config(
        "ACME", "POST https://as2.acme-retail.example/as2/receive AS2-To: ACME-RETAIL", ""
    )
    assert result.ok
    assert any("AS2-To" in m for m in result.data["mismatches"])

    clean = tools.check_identity_config(
        "ACME", "POST https://as2.acme-retail.example/as2/receive AS2-To: ACMERETAIL", ""
    )
    assert clean.data["mismatches"] == []


def test_unparseable_payload():
    result = tools.parse_x12_envelope("not x12")
    assert not result.ok and result.error.code == "UNPARSEABLE_X12"
