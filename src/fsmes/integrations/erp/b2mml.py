"""B2MML-flavored XML for file-based ERP exchange.

B2MML is the XML rendering of ISA-95 that MES↔ERP interfaces standardize on.
This is a deliberately small dialect of it — the two documents that matter:
ProductionSchedule (orders coming down) and ProductionPerformance
(confirmations going up) — carrying the same fields as the JSON payloads.
"""

import xml.etree.ElementTree as ET


def parse_production_schedule(xml_text: str) -> list[dict]:
    """<ProductionSchedule><ProductionRequest><ID>WO-1</ID><Product>FG-COLA</Product>
    <Quantity>100</Quantity><DueDate>2026-08-15</DueDate>...</ProductionRequest>...</...>"""
    root = ET.fromstring(xml_text)
    orders = []
    def _text(request: ET.Element, tag: str) -> str | None:
        node = request.find(tag)
        return node.text if node is not None else None

    for request in root.iter("ProductionRequest"):

        orders.append(
            {
                "code": _text(request, "ID"),
                "material": _text(request, "Product"),
                "quantity": float(_text(request, "Quantity") or 0),
                "due_date": _text(request, "DueDate"),
                "priority": int(_text(request, "Priority") or 50),
            }
        )
    return orders


def _text(value) -> str:
    """Numbers as a person writes them: 5, not 5.0; 2.5 stays 2.5."""
    if isinstance(value, float):
        return format(value, "g")
    return str(value)


def render_production_performance(payload: dict) -> str:
    """The confirmation document sent back to the ERP for one completed order."""
    root = ET.Element("ProductionPerformance")
    response = ET.SubElement(root, "ProductionResponse")
    for tag, key in [
        ("ID", "order"),
        ("ERPReference", "erp_reference"),
        ("Product", "material"),
        ("OrderedQuantity", "ordered_qty"),
        ("GoodQuantity", "good_qty"),
        ("ScrapQuantity", "scrap_qty"),
        ("Lot", "lot"),
        ("StartedAt", "started_at"),
        ("CompletedAt", "completed_at"),
    ]:
        value = payload.get(key)
        if value is not None:
            ET.SubElement(response, tag).text = _text(value)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def render_confirmation(payload: dict) -> str:
    """Either kind of confirmation as ProductionPerformance.

    An order completion renders as it always has (ProductionResponse with
    the totals); an operation confirmation renders as a SegmentResponse
    carrying the step, the cost center, times, quantities and each consumed
    lot - the B2MML shape for "what one segment of work did". Still a
    dialect: no namespace, no schema reference. It says so in the README.
    """
    if payload.get("kind") != "operation_confirmation":
        return render_production_performance(payload)
    root = ET.Element("ProductionPerformance")
    segment = ET.SubElement(root, "SegmentResponse")
    for tag, key in [
        ("ID", "order"), ("ERPReference", "erp_reference"), ("Product", "material"),
        ("SegmentID", "seq"), ("Description", "operation"), ("EquipmentID", "equipment"),
        ("WorkCenter", "work_center"), ("CostCenter", "cost_center"),
        ("InputQuantity", "input_qty"), ("GoodQuantity", "good_qty"), ("ScrapQuantity", "scrap_qty"),
        ("WIPQuantity", "wip_qty"), ("SetupSeconds", "setup_seconds"),
        ("MachineSeconds", "machine_seconds"), ("LabourSeconds", "labour_seconds"),
        ("StartTime", "started_at"), ("EndTime", "completed_at"),
    ]:
        value = payload.get(key)
        if value is not None:
            ET.SubElement(segment, tag).text = _text(value)
    for component in payload.get("components", []):
        node = ET.SubElement(segment, "MaterialConsumedActual")
        ET.SubElement(node, "MaterialLotID").text = str(component["lot"])
        ET.SubElement(node, "MaterialDefinitionID").text = str(component["material"])
        ET.SubElement(node, "Quantity").text = _text(component["quantity"])
        if component.get("equipment"):
            ET.SubElement(node, "EquipmentID").text = str(component["equipment"])
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
