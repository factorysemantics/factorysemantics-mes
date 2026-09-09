"""Quality checks: spec evaluation and automatic non-conformances."""

from fsmes.domain import CheckResult, NcStatus
from fsmes.services import quality


def test_check_in_spec_passes(session):
    check, nc = quality.record_check(
        session, material_code="FG-COLA", characteristic="brix", value=10.2, actor="scott"
    )
    assert check.result is CheckResult.PASS
    assert nc is None


def test_check_out_of_spec_opens_nonconformance(session):
    check, nc = quality.record_check(session, material_code="FG-COLA", characteristic="brix", value=13.0)
    assert check.result is CheckResult.FAIL
    assert nc is not None and nc.status is NcStatus.OPEN
    assert "brix=13.0" in nc.description

    closed = quality.close_nc(session, nc.code, actor="scott")
    assert closed.status is NcStatus.CLOSED
