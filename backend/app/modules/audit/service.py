from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog


def write_audit_log(
    db: Session, actor_user_id: int, action: str, target_type: str, target_id: str,
    extra: Optional[Dict[str, Any]] = None,
) -> AuditLog:
    """Writes and commits immediately -- an audit entry for an action that
    later fails to commit (e.g. the deletion it's logging) would be worse
    than no entry at all, so this is deliberately its own transaction,
    called before the action it records, not after."""
    entry = AuditLog(actor_user_id=actor_user_id, action=action, target_type=target_type, target_id=str(target_id), extra=extra)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
