"""B2MML-flavored XML for file-based ERP exchange.

B2MML is the XML rendering of ISA-95 that MES↔ERP interfaces standardize on.
This is a deliberately small dialect of it — the two documents that matter:
ProductionSchedule (orders coming down) and ProductionPerformance
(confirmations going up) — carrying the same fields as the JSON payloads.

The element-to-field tables below are shared by the writer and the reader,
so a field can never be rendered under one name and read back under
another. `test_erp_confirmation_files.py` runs a confirmation out through
the writer and back through the reader and asserts it survives.
"""

import xml.etree.ElementTree as ET

#: element, payload key, and how to read the text back. `str` covers the
#: timestamps too: the contract models parse ISO-8601 themselves, and a
#: reader that half-parsed dates would be a second place for them to drift.
COMPLETION_FIELDS: tuple[tuple[str, str, type], ...] = (
    # The idempotency key, first because it is the first thing a collector
    # should read. It was missing from this dialect until 2026-09-10, which
    # meant a folder of confirmations carried no way to tell a re-sent file
    # from a second confirmation; `parse_confirmation` rebuilds it for files
    # written before then.
    ("MessageKey", "message_key", str),
    ("ID", "order", str),
    ("ERPReference", "erp_reference", str),
    ("Product", "material", str),
    ("OrderedQuantity", "ordered_qty", float),
    ("GoodQuantity", "good_qty", float),
    ("ScrapQuantity", "scrap_qty", float),
    # Zero on an order that stopped where it was asked to, and written
    # anyway: a reader who never sees the element cannot tell an order
    # that did not over-run from one whose over-run was not reported.
    ("OverQuantity", "over_qty", float),
    ("Lot", "lot", str),
    ("StartedAt", "started_at", str),
    ("CompletedAt", "completed_at", str),
)

SEGMENT_FIELDS: tuple[tuple[str, str, type], ...] = (
    ("MessageKey", "message_key", str),
    ("ID", "order", str),
    ("ERPReference", "erp_reference", str),
    ("Product", "material", str),
    ("SegmentID", "seq", int),
    ("Description", "operation", str),
    ("EquipmentID", "equipment", str),
    ("WorkCenter", "work_center", str),
    ("CostCenter", "cost_center", str),
    ("InputQuantity", "input_qty", float),
    ("GoodQuantity", "good_qty", float),
    ("ScrapQuantity", "scrap_qty", float),
    ("WIPQuantity", "wip_qty", float),
    ("SetupSeconds", "setup_seconds", float),
    ("MachineSeconds", "machine_seconds", float),
    ("LabourSeconds", "labour_seconds", float),
    ("StartTime", "started_at", str),
    ("EndTime", "completed_at", str),
)

COMPONENT_FIELDS: tuple[tuple[str, str, type], ...] = (
    ("MaterialLotID", "lot", str),
    ("MaterialDefinitionID", "material", str),
    ("Quantity", "quantity", float),
    ("EquipmentID", "equipment", str),
)


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


def _write(parent: ET.Element, payload: dict, fields) -> None:
    for tag, key, _reader in fields:
        value = payload.get(key)
        if value is not None:
            ET.SubElement(parent, tag).text = _text(value)


def _read(node: ET.Element, fields) -> dict:
    """Elements back to payload keys. An element that is present but empty
    reads as absent rather than as an empty string, because that is what a
    self-closed tag means; an element that will not convert keeps its text,
    so the contract's own validation reports it rather than this reader
    raising on a file it was asked to read."""
    payload: dict = {}
    for tag, key, reader in fields:
        found = node.find(tag)
        if found is None or found.text is None or not found.text.strip():
            continue
        text = found.text.strip()
        try:
            payload[key] = reader(text)
        except ValueError:
            payload[key] = text
    return payload


def render_production_performance(payload: dict) -> str:
    """The confirmation document sent back to the ERP for one completed order."""
    root = ET.Element("ProductionPerformance")
    response = ET.SubElement(root, "ProductionResponse")
    _write(response, payload, COMPLETION_FIELDS)
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
    _write(segment, payload, SEGMENT_FIELDS)
    for component in payload.get("components", []):
        node = ET.SubElement(segment, "MaterialConsumedActual")
        _write(node, component, COMPONENT_FIELDS)
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def parse_confirmation(xml_text: str) -> dict:
    """A ProductionPerformance document back into the JSON payload.

    The inverse of `render_confirmation`, and the reason `fsmes erp
    validate` can check an outbox full of XML rather than only the JSON
    the REST connectors post. `kind` comes from the element name, because
    that is where the XML keeps it.
    """
    root = ET.fromstring(xml_text)
    segment = root.find(".//SegmentResponse")
    if segment is not None:
        payload = {"kind": "operation_confirmation", **_read(segment, SEGMENT_FIELDS)}
        payload["components"] = [_read(node, COMPONENT_FIELDS)
                                 for node in segment.findall("MaterialConsumedActual")]
        return _keyed(payload, f"op{payload.get('seq')}")
    response = root.find(".//ProductionResponse")
    if response is None:
        raise ValueError(
            "not a confirmation: expected a ProductionPerformance document with a "
            "SegmentResponse (one operation) or a ProductionResponse (one order)")
    return _keyed({"kind": "order_completion", **_read(response, COMPLETION_FIELDS)}, "completion")


def _keyed(payload: dict, suffix: str) -> dict:
    """Rebuild `message_key` for a file written before it was in the dialect.

    The rule is the one that made the key in the first place, so the key a
    reader rebuilds is the key the MES would have written. Nothing is
    invented: if the order is missing too, the key stays missing and the
    contract reports it.
    """
    if payload.get("message_key") or not payload.get("order"):
        return payload
    payload["message_key"] = f"{payload['order']}:{suffix}"
    return payload
