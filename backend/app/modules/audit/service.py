import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEvent


def record(
    db: Session,
    *,
    entity_type: str,
    entity_id: Any,
    action: str,
    actor_id: uuid.UUID | None,
    **payload: Any,
) -> None:
    db.add(
        AuditEvent(
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            actor_id=actor_id,
            payload=payload,
        )
    )
