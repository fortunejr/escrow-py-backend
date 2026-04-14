from uuid import uuid4

from django.db import IntegrityError

from core.audit import log_audit_event
from core.models import AuditLog

from .models import PayoutRecord


def generate_payout_reference(escrow_id):
    return f"payout_{escrow_id}_{uuid4().hex[:16]}"


def create_payout_record_for_release(escrow, initiated_by, currency="NGN", metadata=None):
    payload = metadata or {}

    for _ in range(5):
        reference = generate_payout_reference(escrow.id)
        try:
            payout = PayoutRecord.objects.create(
                escrow=escrow,
                reference=reference,
                amount=escrow.amount,
                currency=currency,
                status=PayoutRecord.Status.PENDING,
                initiated_by=initiated_by,
                metadata=payload,
            )
            log_audit_event(
                actor=initiated_by,
                action=AuditLog.Action.RELEASE_TRIGGERED,
                object_type="escrow",
                object_id=escrow.id,
                metadata={
                    "payout_id": payout.id,
                    "payout_reference": payout.reference,
                    "payout_status": payout.status,
                    "amount": str(payout.amount),
                    "currency": payout.currency,
                    "trigger": payload.get("trigger"),
                },
            )
            return payout
        except IntegrityError:
            continue

    raise ValueError("Unable to generate unique payout reference.")
