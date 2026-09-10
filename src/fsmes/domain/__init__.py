"""Domain model — importing this package registers every table on Base.metadata."""

from fsmes.domain.adjustments import AdjustmentStatus, RecommendedAdjustment
from fsmes.domain.audit import AuditLog
from fsmes.domain.calendar import (
    CalendarException,
    ExceptionKind,
    ShiftPattern,
)
from fsmes.domain.documents import Document, DocumentStatus
from fsmes.domain.equipment import EquipmentState, EquipmentStateName
from fsmes.domain.execution import (
    LotConsumption,
    LotStatus,
    MaterialLot,
    ProductionLog,
    ProductionSource,
)
from fsmes.domain.gauges import (
    Calibration,
    CalibrationResult,
    Gauge,
    GaugeStatus,
)
from fsmes.domain.idempotency import IdempotencyKey
from fsmes.domain.inbound import InboundEvent, InboundKind
from fsmes.domain.integration import ErpMessage, MessageDirection, MessageStatus
from fsmes.domain.maintenance import (
    MaintenanceKind,
    MaintenanceOrder,
    MaintenancePlan,
    MaintenanceStatus,
    TriggerKind,
)
from fsmes.domain.masterdata import (
    BomItem,
    Equipment,
    EquipmentLevel,
    Material,
    MaterialType,
    Person,
    Role,
    Routing,
    RoutingOperation,
)
from fsmes.domain.quality import CheckResult, NcStatus, NonConformance, QualityCheck, QualitySpec
from fsmes.domain.scheduling import ScheduledSlot, SlotKind
from fsmes.domain.serialization import (
    SerialSequence,
    SerialUnit,
    UnitComponent,
    UnitInspection,
    UnitStatus,
)
from fsmes.domain.timeseries import TagValue
from fsmes.domain.triggers import Trigger, TriggerCondition, TriggerFiring, TriggerStatus
from fsmes.domain.uns import UnsPublication
from fsmes.domain.workorders import OperationStatus, OrderStatus, WorkOrder, WorkOrderOperation

__all__ = [
    "AdjustmentStatus",
    "AuditLog",
    "BomItem",
    "CalendarException",
    "Calibration",
    "CalibrationResult",
    "CheckResult",
    "Document",
    "DocumentStatus",
    "Equipment",
    "EquipmentLevel",
    "EquipmentState",
    "EquipmentStateName",
    "ErpMessage",
    "ExceptionKind",
    "Gauge",
    "GaugeStatus",
    "IdempotencyKey",
    "InboundEvent",
    "InboundKind",
    "LotConsumption",
    "LotStatus",
    "MaintenanceKind",
    "MaintenanceOrder",
    "MaintenancePlan",
    "MaintenanceStatus",
    "Material",
    "MaterialLot",
    "MaterialType",
    "MessageDirection",
    "MessageStatus",
    "NcStatus",
    "NonConformance",
    "OperationStatus",
    "OrderStatus",
    "Person",
    "ProductionLog",
    "ProductionSource",
    "QualityCheck",
    "QualitySpec",
    "RecommendedAdjustment",
    "Role",
    "Routing",
    "RoutingOperation",
    "ScheduledSlot",
    "SerialSequence",
    "SerialUnit",
    "ShiftPattern",
    "SlotKind",
    "TagValue",
    "Trigger",
    "TriggerCondition",
    "TriggerFiring",
    "TriggerKind",
    "TriggerStatus",
    "UnitComponent",
    "UnitInspection",
    "UnitStatus",
    "UnsPublication",
    "WorkOrder",
    "WorkOrderOperation",
]
