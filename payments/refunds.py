from decimal import Decimal
from uuid import uuid4

from django.db import IntegrityError

from core.audit import log_audit_event
from core.models import AuditLog

from .models import RefundRecord
from .paystack import PaystackPayoutError
from .paystack import initiate_paystack_refund


class RefundExecutionError(Exception):
    pass


def generate_refund_reference(escrow_id):
    return f"refund_{escrow_id}_{uuid4().hex[:16]}"


def create_refund_record_for_escrow(escrow, initiated_by, payment_reference, reason=""):
    metadata = {
        "payment_reference": payment_reference,
        "execution": "pending_paystack_refund",
    }

    for _ in range(5):
        reference = generate_refund_reference(escrow.id)
        try:
            refund = RefundRecord.objects.create(
                escrow=escrow,
                reference=reference,
                amount=escrow.amount,
                currency="NGN",
                status=RefundRecord.Status.PENDING,
                reason=reason or "",
                initiated_by=initiated_by,
                metadata=metadata,
            )
            log_audit_event(
                actor=initiated_by,
                action=AuditLog.Action.REFUND_TRIGGERED,
                object_type="escrow",
                object_id=escrow.id,
                metadata={
                    "refund_id": refund.id,
                    "refund_reference": refund.reference,
                    "payment_reference": payment_reference,
                    "refund_status": refund.status,
                    "amount": str(refund.amount),
                    "currency": refund.currency,
                },
            )
            return refund
        except IntegrityError:
            continue

    raise RefundExecutionError("Unable to generate unique refund reference.")


def map_paystack_refund_status(paystack_status):
    normalized = str(paystack_status or "").lower()
    if normalized == "success":
        return RefundRecord.Status.SUCCESS
    if normalized in {"processing", "pending"}:
        return RefundRecord.Status.PROCESSING
    if normalized == "reversed":
        return RefundRecord.Status.REVERSED
    if normalized == "failed":
        return RefundRecord.Status.FAILED
    return RefundRecord.Status.PROCESSING


def execute_paystack_refund_for_payment(payment_reference, amount):
    amount_kobo = int((Decimal(amount) * Decimal("100")).quantize(Decimal("1")))
    try:
        return initiate_paystack_refund(transaction_reference=payment_reference, amount_kobo=amount_kobo)
    except PaystackPayoutError as exc:
        raise RefundExecutionError(str(exc)) from exc
