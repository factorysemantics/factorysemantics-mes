"""Demo plant: one site, one packaging line with two machines, a cola product.

The seeded equipment codes (MIX01, PACK01) match config/tag_map.json and the
OPC simulator, so a seeded database is immediately runnable end-to-end.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from fsmes.config import get_settings
from fsmes.domain import (
    BomItem,
    Equipment,
    EquipmentLevel,
    Material,
    MaterialLot,
    MaterialType,
    Person,
    QualitySpec,
    Routing,
    RoutingOperation,
)
from fsmes.services.auth import hash_password


def seed_demo_plant(session: Session) -> bool:
    """Populate the demo plant. Returns False if data already exists."""
    if session.scalar(select(Equipment.id).limit(1)):
        return False
    settings = get_settings()

    ent = Equipment(code="ACME", name="ACME Beverages", level=EquipmentLevel.ENTERPRISE)
    site = Equipment(code="KC1", name="Kansas City Plant", level=EquipmentLevel.SITE, parent=ent)
    area = Equipment(code="PKG", name="Packaging", level=EquipmentLevel.AREA, parent=site)
    line = Equipment(code="LINE1", name="Packaging Line 1", level=EquipmentLevel.WORK_CENTER, parent=area)
    mixer = Equipment(
        code="MIX01", name="Mixer 01", level=EquipmentLevel.WORK_UNIT, parent=line, ideal_cycle_seconds=4.0
    )
    packer = Equipment(
        code="PACK01", name="Packer 01", level=EquipmentLevel.WORK_UNIT, parent=line, ideal_cycle_seconds=3.0
    )

    sugar = Material(code="RAW-SUGAR", name="Sugar", unit="kg", type=MaterialType.RAW)
    flavor = Material(code="RAW-FLAVOR", name="Cola Flavor Concentrate", unit="l", type=MaterialType.RAW)
    cola = Material(code="FG-COLA", name="Cola Syrup 1L", unit="ea", type=MaterialType.FINISHED)

    routing = Routing(code="RT-COLA", name="Make Cola Syrup", material=cola)
    routing.operations = [
        RoutingOperation(seq=10, name="Mix", equipment=mixer),
        RoutingOperation(seq=20, name="Pack", equipment=packer),
    ]

    session.add_all(
        [
            ent,
            site,
            area,
            line,
            mixer,
            packer,
            sugar,
            flavor,
            cola,
            BomItem(parent=cola, component=sugar, quantity=0.5),
            BomItem(parent=cola, component=flavor, quantity=0.1),
            routing,
            Person(
                code="ADMIN",
                name="Plant Administrator",
                role="admin",
                password_hash=hash_password(settings.admin_password),
            ),
            Person(
                code="SCOTT",
                name="Scott",
                role="operator",
                password_hash=hash_password(settings.operator_password),
            ),
            QualitySpec(material=cola, characteristic="brix", unit="°Bx", min_value=9.5, max_value=11.5),
            MaterialLot(code="LOT-SUGAR-001", material=sugar, quantity=500, original_quantity=500),
            MaterialLot(code="LOT-FLAVOR-001", material=flavor, quantity=100, original_quantity=100),
        ]
    )
    return True
