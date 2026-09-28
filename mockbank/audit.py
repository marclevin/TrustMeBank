"""Audit log helper. Call inside the transaction that performs the action."""

from sqlalchemy.orm import Session

from mockbank.models import AuditLog


def audit(
    db: Session,
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
            ip=ip,
        )
    )
