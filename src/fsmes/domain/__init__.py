"""Domain model — importing this package registers every table on Base.metadata."""

from fsmes.domain.adjustments import AdjustmentStatus, RecommendedAdjustment
from fsmes.domain.ai_turns import BRAINS, AiTurn
from fsmes.domain.audit import AuditLog
from fsmes.domain.calendar import (
    CalendarException,
    ExceptionKind,
    ShiftPattern,
)
from fsmes.domain.crew import (
    DISPATCHABLE_LEVEL,
    LEVEL_COMPETENT,
    LEVEL_EXPERT,
    LEVEL_TRAINEE,
    DispatchRule,
    DispatchStrategy,
    PersonnelSkill,
    RosterEntry,
    Skill,
    UnassignedReason,
)
from fsmes.domain.documents import Document, DocumentStatus
from fsmes.domain.equipment import (
    ConnectionStateName,
    EquipmentConnection,
    EquipmentState,
    EquipmentStateName,
)
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
from fsmes.domain.inbound import InboundEvent, InboundKind, InboundWatermark
from fsmes.domain.integration import ErpMessage, MessageDirection, MessageStatus
from fsmes.domain.maintenance import (
    DEFAULT_PRIORITY,
    OPEN_STATUSES,
    PRIORITY_PRODUCTION_CRITICAL,
    PRIORITY_ROUTINE,
    PRIORITY_SAFETY,
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
from fsmes.domain.plant_settings import PlantSetting
from fsmes.domain.quality import (
    CheckResult,
    NcDisposition,
    NcStatus,
    NonConformance,
    QualityCheck,
    QualitySample,
    QualitySpec,
    SpcSignal,
)
from fsmes.domain.reasons import DowntimeReason, DowntimeReasonStatus
from fsmes.domain.scheduling import ScheduledSlot, SlotKind
from fsmes.domain.serialization import (
    SerialSequence,
    SerialUnit,
    UnitComponent,
    UnitInspection,
    UnitStatus,
)
from fsmes.domain.severities import NcSeverity, NcSeverityStatus
from fsmes.domain.timeseries import TagValue
from fsmes.domain.triggers import Trigger, TriggerCondition, TriggerFiring, TriggerStatus
from fsmes.domain.uns import UnsPublication
from fsmes.domain.workorders import OperationStatus, OrderStatus, WorkOrder, WorkOrderOperation

__all__ = [
    "BRAINS",
    "DEFAULT_PRIORITY",
    "DISPATCHABLE_LEVEL",
    "LEVEL_COMPETENT",
    "LEVEL_EXPERT",
    "LEVEL_TRAINEE",
    "OPEN_STATUSES",
    "PRIORITY_PRODUCTION_CRITICAL",
    "PRIORITY_ROUTINE",
    "PRIORITY_SAFETY",
    "AdjustmentStatus",
    "AiTurn",
    "AuditLog",
    "BomItem",
    "CalendarException",
    "Calibration",
    "CalibrationResult",
    "CheckResult",
    "ConnectionStateName",
    "Document",
    "DocumentStatus",
    "DowntimeReason",
    "DowntimeReasonStatus",
    "DispatchRule",
    "DispatchStrategy",
    "Equipment",
    "EquipmentConnection",
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
    "InboundWatermark",
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
    "NcDisposition",
    "NcSeverity",
    "NcSeverityStatus",
    "NcStatus",
    "NonConformance",
    "OperationStatus",
    "OrderStatus",
    "Person",
    "PersonnelSkill",
    "PlantSetting",
    "ProductionLog",
    "ProductionSource",
    "QualityCheck",
    "QualitySample",
    "QualitySpec",
    "RecommendedAdjustment",
    "Role",
    "RosterEntry",
    "Routing",
    "RoutingOperation",
    "ScheduledSlot",
    "SerialSequence",
    "SerialUnit",
    "ShiftPattern",
    "Skill",
    "SlotKind",
    "SpcSignal",
    "TagValue",
    "Trigger",
    "TriggerCondition",
    "TriggerFiring",
    "TriggerKind",
    "TriggerStatus",
    "UnitComponent",
    "UnitInspection",
    "UnassignedReason",
    "UnitStatus",
    "UnsPublication",
    "WorkOrder",
    "WorkOrderOperation",
]
