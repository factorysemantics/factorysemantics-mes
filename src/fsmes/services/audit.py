"""The one way anything gets written to the audit trail."""

from sqlalchemy.orm import Session

from fsmes.domain import AuditLog


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
    session.add(
        AuditLog(
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
    )
