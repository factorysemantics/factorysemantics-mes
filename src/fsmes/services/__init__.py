"""Business logic. Services own all rules and all audit writes; the API and the
integrations (OPC agent, ERP sync) are thin callers of this layer.

Error contract, mapped to HTTP by the API:
    NotFound -> 404, Conflict -> 409, Invalid -> 400,
    WrongSampleSize -> 422
"""


class MesError(Exception):
    """Base for all expected business errors."""


class NotFound(MesError):
    pass


class Conflict(MesError):
    """State transition or uniqueness violation."""


class Invalid(MesError):
    """Bad input."""


class WrongSampleSize(Invalid):
    """A sample arrived with a different number of readings than the plan asks.

    A kind of `Invalid`, so nothing that already handles bad input has to
    learn about it, but answered **422** rather than 400: the request is well
    formed, the plant is in a fine state, and the one thing wrong with it is
    that the body does not match the sampling plan written on the
    specification - which only this plant knows. The sentence carries both
    numbers, so a caller can fix the body without reading anything else.
    """
