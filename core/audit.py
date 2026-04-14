from .models import AuditLog


def log_audit_event(actor, action, object_type, object_id=None, metadata=None, object_ref=None):
    if not object_ref:
        if object_id is not None:
            object_ref = f"{object_type}:{object_id}"
        else:
            object_ref = object_type

    payload = metadata if isinstance(metadata, dict) else {}

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        object_ref=object_ref,
        metadata=payload,
    )
