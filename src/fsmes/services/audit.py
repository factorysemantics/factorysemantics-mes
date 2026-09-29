"""The one way anything gets written to the audit trail."""

import logging

from sqlalchemy.orm import Session

from fsmes.db import utcnow
from fsmes.domain import AuditLog

LOGGER = logging.getLogger(__name__)


def record(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    moment = utcnow()
    row = AuditLog(
        ts=moment,
        actor=actor,
        # An agent's actor carries who it acts for (api.deps.Actor); a
        # plain string is a person acting as themselves.
        on_behalf_of=getattr(actor, "on_behalf_of", None),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
    )
    _stamp_shift(session, row, moment)
    session.add(row)


def _stamp_shift(session: Session, row: AuditLog, moment) -> None:
    """Which shift this action fell in, from the plant calendar as it stands.

    Written here rather than read back later for the reason decision 0028
    gives: the roster changes, and a row stamped at write time says which
    shift the plant was running *then*. There is no equipment on an audit
    row, so it is the site-wide patterns that answer - the same call every
    floor table makes with a machine named.

    Imported inside the function because `services.calendar` imports this
    module, and an audit row must not depend on the import order of the
    thing it is recording.

    **A shift lookup never costs a plant its audit row.** If the calendar
    cannot be read the row is written unattributed and the failure is a line
    in the log: an audit trail that refused a write because it could not
    name a shift would be the tail wagging the dog.
    """
    from fsmes.services import calendar

    try:
        calendar.stamp(row, calendar.shift_for(session, moment))
    except Exception as exc:        # the record outranks the attribution
        LOGGER.warning("audit: this row has no shift on it (%s) action=%s",
                       type(exc).__name__, row.action, exc_info=exc)
        row.shift_code = row.shift_day = None
