"""The certificate of analysis: issued at the end of the line, immutable,
superseded rather than edited, and true to the records it is built from.
"""

import pytest
from sqlalchemy import select

from fsmes.domain import Document, DocumentStatus, ProductionSource
from fsmes.services import coa, documents, execution, quality, serialization, workorders


def _finished_order(session, code="WO-COA-1"):
    wo = workorders.create(session, code=code, material_code="FG-COLA", quantity=4, actor="test")
    workorders.release(session, code, "test")
    first = sorted(wo.operations, key=lambda o: o.seq)[0]
    lot = execution.create_lot(session, code="LOT-COA-SYRUP", material_code=first.order.material.code,
                               quantity=10, actor="test")
    workorders.start_operation(session, code, first.seq, actor="test")
    execution.consume(session, order_code=code, lot_code=lot.code, quantity=2, seq=first.seq, actor="test")
    quality.record_check(session, material_code="FG-COLA", characteristic="brix", value=10.2,
                         work_order_code=code, actor="qa")
    quality.record_check(session, material_code="FG-COLA", characteristic="brix", value=13.0,
                         work_order_code=code, actor="qa")   # out of spec: opens an NC
    serialization.produce(session, material_code="FG-COLA", order_code=code, serial="BTL-COA-1", actor="test")
    execution.report(session, equipment_code="MIX01", good=4, source=ProductionSource.OPC)
    execution.report(session, equipment_code="PACK01", good=4, source=ProductionSource.OPC)
    session.flush()
    return wo


def test_completing_an_order_issues_its_certificate_with_the_evidence(session):
    wo = _finished_order(session)
    assert wo.status.value == "completed"
    cert = coa.latest(session, wo.code)
    assert cert["revision"] == 1 and cert["document"] == "COA-WO-COA-1"
    body = cert["body"]
    assert "Certificate of analysis" in body and "WO-COA-1-FG" in body
    assert "LOT-COA-SYRUP" in body and "brix" in body and "NC-" in body
    assert "BTL-COA-1" in body
    doc = session.scalar(select(Document).where(Document.code == "COA-WO-COA-1"))
    assert doc.status is DocumentStatus.APPROVED and doc.approved_by == "system"
    data = coa.gather(session, wo.code)
    assert data["produced_lot"] == "WO-COA-1-FG" and data["consumed"][0]["lot"] == "LOT-COA-SYRUP"
    [ch] = data["characteristics"]
    assert (ch["n"], ch["failed"], ch["min"], ch["max"]) == (2, 1, 10.2, 13.0)
    assert data["nonconformances"][0]["status"] == "open"


def test_a_certificate_is_immutable_and_reissue_supersedes_it_by_name(session):
    wo = _finished_order(session, "WO-COA-2")
    first = documents.current(session, "COA-WO-COA-2")
    quality.record_check(session, material_code="FG-COLA", characteristic="brix", value=10.5,
                         work_order_code=wo.code, actor="qa")
    again = coa.issue(session, wo.code, actor="sup")
    assert again.revision == 2 and "supersedes revision 1" in again.body
    session.refresh(first)
    assert first.status is DocumentStatus.SUPERSEDED and "supersedes" not in first.body
    assert coa.latest(session, wo.code)["revisions"] == [1, 2]
    assert "3 |" in again.body.split("## Quality")[1].split("## Non-conformances")[0], "the new check is on it"


def test_an_unfinished_order_has_no_certificate_unless_asked_for_explicitly(session):
    wo = workorders.create(session, code="WO-COA-3", material_code="FG-COLA", quantity=1, actor="test")
    with pytest.raises(Exception, match="issued when the order completes"):
        coa.issue(session, wo.code, actor="sup")
    with pytest.raises(Exception, match="no certificate"):
        coa.latest(session, wo.code)
    doc = coa.issue(session, wo.code, actor="sup", allow_incomplete=True)
    assert doc.revision == 1


def test_certificates_do_not_crowd_the_instruction_catalogue(session):
    _finished_order(session, "WO-COA-4")
    codes = [row["code"] for row in documents.catalogue(session)]
    assert "COA-WO-COA-4" not in codes


def test_the_api_serves_and_gates_certificates(session, sign_in):
    _finished_order(session, "WO-COA-5")
    op = sign_in("OP-COA", role="operator")
    assert op.get("/coa/WO-COA-5").json()["revision"] == 1
    assert op.get("/coa").json()["items"][0]["order"] == "WO-COA-5"
    assert "characteristics" in op.get("/coa/WO-COA-5/data").json()
    assert op.post("/coa/WO-COA-5").status_code == 403
    sup = sign_in("SUP-COA", role="supervisor")
    assert sup.post("/coa/WO-COA-5").json()["issued_revision"] == 2
    assert op.get("/coa/WO-NOPE").status_code == 404
