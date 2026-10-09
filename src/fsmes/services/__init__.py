"""Business logic. Services own all rules and all audit writes; the API and the
integrations (OPC agent, ERP sync) are thin callers of this layer.

Error contract, mapped to HTTP by the API:
    NotFound -> 404, Conflict -> 409, Invalid -> 400,
    WrongSampleSize -> 422, Forbidden -> 403
"""


class MesError(Exception):
    """Base for all expected business errors."""


class NotFound(MesError):
    pass


class Conflict(MesError):
    """State transition or uniqueness violation."""


class Invalid(MesError):
    """Bad input."""


class Forbidden(MesError):
    """Signed in, allowed to use this route, and not allowed to do *this*.

    Not the same as the capability check in `api.deps.require`, which answers
    403 before a service is ever called: that one says "your account does not
    do this kind of thing at all". This one says "your account does this kind
    of thing, but not to this row" - the maintenance order that is assigned to
    somebody else is the first case. The sentence always names who *can*, so
    the person reading it knows what to do next rather than only that they
    were refused.
    """


class WrongSampleSize(Invalid):
    """A sample arrived with a different number of readings than the plan asks.

    A kind of `Invalid`, so nothing that already handles bad input has to
    learn about it, but answered **422** rather than 400: the request is well
    formed, the plant is in a fine state, and the one thing wrong with it is
    that the body does not match the sampling plan written on the
    specification - which only this plant knows. The sentence carries both
    numbers, so a caller can fix the body without reading anything else.
    """
