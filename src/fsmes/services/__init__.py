"""Business logic. Services own all rules and all audit writes; the API and the
integrations (OPC agent, ERP sync) are thin callers of this layer.

Error contract, mapped to HTTP by the API:
    NotFound -> 404, Conflict -> 409, Invalid -> 400
"""


class MesError(Exception):
    """Base for all expected business errors."""


class NotFound(MesError):
    pass


class Conflict(MesError):
    """State transition or uniqueness violation."""


class Invalid(MesError):
    """Bad input."""
