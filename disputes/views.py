from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.audit import log_audit_event
from core.models import AuditLog
from escrow.models import EscrowTransaction
from payments.models import PaymentRecord, PayoutRecord, RefundRecord
from payments.payouts import create_payout_record_for_release
from payments.refunds import (
    RefundExecutionError,
    create_refund_record_for_escrow,
    execute_paystack_refund_for_payment,
    map_paystack_refund_status,
)

from .models import Dispute
from .serializers import DisputeSerializer


def build_response(success, message, data=None, errors=None):
    return {
        "success": success,
        "message": message,
        "data": data,
        "errors": errors,
    }


def payout_payload(record):
    return {
        "id": record.id,
        "escrow_id": record.escrow_id,
        "reference": record.reference,
        "provider_reference": record.provider_reference,
        "amount": str(record.amount),
        "currency": record.currency,
        "status": record.status,
        "initiated_by": record.initiated_by_id,
        "processed_at": record.processed_at,
        "created_at": record.created_at,
    }


def refund_payload(record):
    return {
        "id": record.id,
        "escrow_id": record.escrow_id,
        "reference": record.reference,
        "provider_reference": record.provider_reference,
        "amount": str(record.amount),
        "currency": record.currency,
        "status": record.status,
        "reason": record.reason,
        "initiated_by": record.initiated_by_id,
        "processed_at": record.processed_at,
        "created_at": record.created_at,
    }


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_dispute_view(request):
    """Open a dispute for an eligible escrow by its buyer or seller."""
    escrow_id = request.data.get("escrow_id")
    reason = request.data.get("reason")

    errors = {}

    if escrow_id is None:
        errors["escrow_id"] = ["escrow_id is required."]
    if not reason or not isinstance(reason, str):
        errors["reason"] = ["reason is required."]

    if errors:
        return Response(
            build_response(False, "Dispute creation failed.", data=None, errors=errors),
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        escrow_id = int(escrow_id)
    except (TypeError, ValueError):
        return Response(
            build_response(
                False,
                "Dispute creation failed.",
                data=None,
                errors={"escrow_id": ["escrow_id must be a valid integer."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    with transaction.atomic():
        escrow = (
            EscrowTransaction.objects.select_for_update()
            .select_related("buyer", "seller")
            .filter(id=escrow_id)
            .first()
        )
        if not escrow:
            return Response(
                build_response(False, "Escrow not found.", data=None, errors={"escrow": ["Escrow does not exist."]}),
                status=status.HTTP_404_NOT_FOUND,
            )

        if request.user.id not in {escrow.buyer_id, escrow.seller_id}:
            return Response(
                build_response(
                    False,
                    "Permission denied.",
                    data=None,
                    errors={"permission": ["Only escrow buyer or seller can raise dispute."]},
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        eligible_statuses = {
            EscrowTransaction.Status.FUNDED,
            EscrowTransaction.Status.RELEASED,
        }
        if escrow.status not in eligible_statuses and escrow.status != EscrowTransaction.Status.DISPUTED:
            return Response(
                build_response(
                    False,
                    "Dispute creation failed.",
                    data=None,
                    errors={"escrow": [f"Escrow cannot be disputed in '{escrow.status}' status."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing_open = (
            Dispute.objects.select_for_update()
            .filter(escrow=escrow, status=Dispute.Status.OPEN)
            .first()
        )
        if existing_open:
            return Response(
                build_response(
                    False,
                    "Dispute creation failed.",
                    data=None,
                    errors={"dispute": ["An open dispute already exists for this escrow."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        dispute = Dispute.objects.create(
            escrow=escrow,
            raised_by=request.user,
            reason=reason.strip(),
            status=Dispute.Status.OPEN,
        )

        if escrow.status != EscrowTransaction.Status.DISPUTED:
            escrow.status = EscrowTransaction.Status.DISPUTED
            escrow.save(update_fields=["status", "updated_at"])

        log_audit_event(
            actor=request.user,
            action=AuditLog.Action.DISPUTE_OPENED,
            object_type="dispute",
            object_id=dispute.id,
            metadata={
                "escrow_id": escrow.id,
                "raised_by": dispute.raised_by_id,
                "status": dispute.status,
                "escrow_status": escrow.status,
            },
        )

    return Response(
        build_response(True, "Dispute created successfully.", data=DisputeSerializer(dispute).data, errors=None),
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_disputes_view(request):
    """List disputes visible to the authenticated user (all for admin)."""
    if request.user.is_staff:
        disputes = Dispute.objects.select_related("escrow", "raised_by").all()
    else:
        disputes = (
            Dispute.objects.select_related("escrow", "raised_by")
            .filter(
                Q(raised_by=request.user)
                | Q(escrow__buyer=request.user)
                | Q(escrow__seller=request.user)
            )
            .distinct()
        )

    data = DisputeSerializer(disputes, many=True).data
    return Response(
        build_response(True, "Disputes fetched successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dispute_detail_view(request, dispute_id):
    """Return one dispute if requester is involved or an admin."""
    dispute = Dispute.objects.select_related("escrow", "raised_by").filter(id=dispute_id).first()
    if not dispute:
        return Response(
            build_response(False, "Dispute not found.", data=None, errors={"dispute": ["Dispute does not exist."]}),
            status=status.HTTP_404_NOT_FOUND,
        )

    if not request.user.is_staff and request.user.id not in {
        dispute.raised_by_id,
        dispute.escrow.buyer_id,
        dispute.escrow.seller_id,
    }:
        return Response(
            build_response(
                False,
                "Permission denied.",
                data=None,
                errors={"permission": ["You cannot access this dispute."]},
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    return Response(
        build_response(True, "Dispute fetched successfully.", data=DisputeSerializer(dispute).data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def resolve_dispute_view(request, dispute_id):
    """Admin-only dispute resolution with release/refund outcomes."""
    if not request.user.is_staff:
        return Response(
            build_response(
                False,
                "Permission denied.",
                data=None,
                errors={"permission": ["Only admin users can resolve disputes."]},
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    outcome = request.data.get("outcome")
    resolution_notes = request.data.get("resolution_notes", "")

    if not outcome or not isinstance(outcome, str):
        return Response(
            build_response(
                False,
                "Dispute resolution failed.",
                data=None,
                errors={"outcome": ["outcome is required."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    outcome = outcome.strip().lower()
    allowed = {Dispute.ResolutionOutcome.RELEASE, Dispute.ResolutionOutcome.REFUND}
    if outcome not in allowed:
        return Response(
            build_response(
                False,
                "Dispute resolution failed.",
                data=None,
                errors={"outcome": ["outcome must be 'release' or 'refund'."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    if resolution_notes is not None and not isinstance(resolution_notes, str):
        return Response(
            build_response(
                False,
                "Dispute resolution failed.",
                data=None,
                errors={"resolution_notes": ["resolution_notes must be a string."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )
    resolution_notes = (resolution_notes or "").strip()

    if outcome == Dispute.ResolutionOutcome.RELEASE:
        with transaction.atomic():
            dispute = (
                Dispute.objects.select_for_update()
                .select_related("escrow", "escrow__buyer", "escrow__seller")
                .filter(id=dispute_id)
                .first()
            )
            if not dispute:
                return Response(
                    build_response(False, "Dispute not found.", data=None, errors={"dispute": ["Dispute does not exist."]}),
                    status=status.HTTP_404_NOT_FOUND,
                )

            if dispute.status != Dispute.Status.OPEN:
                return Response(
                    build_response(
                        False,
                        "Dispute resolution failed.",
                        data=None,
                        errors={"dispute": ["This dispute has already been resolved."]},
                    ),
                    status=status.HTTP_400_BAD_REQUEST,
                )

            escrow = EscrowTransaction.objects.select_for_update().filter(id=dispute.escrow_id).first()
            if escrow.status != EscrowTransaction.Status.DISPUTED:
                return Response(
                    build_response(
                        False,
                        "Dispute resolution failed.",
                        data=None,
                        errors={"escrow": [f"Dispute resolution requires escrow in 'disputed' status, got '{escrow.status}'."]},
                    ),
                    status=status.HTTP_400_BAD_REQUEST,
                )

            existing_payout = (
                PayoutRecord.objects.select_for_update()
                .filter(
                    escrow=escrow,
                    status__in=[
                        PayoutRecord.Status.PENDING,
                        PayoutRecord.Status.PROCESSING,
                        PayoutRecord.Status.SUCCESS,
                    ],
                )
                .first()
            )
            if existing_payout:
                return Response(
                    build_response(
                        False,
                        "Dispute resolution failed.",
                        data=None,
                        errors={"payout": ["A payout has already been triggered for this escrow."]},
                    ),
                    status=status.HTTP_400_BAD_REQUEST,
                )

            payout = create_payout_record_for_release(
                escrow=escrow,
                initiated_by=request.user,
                currency="NGN",
                metadata={
                    "trigger": "admin_dispute_resolution",
                    "dispute_id": dispute.id,
                    "outcome": Dispute.ResolutionOutcome.RELEASE,
                },
            )

            escrow.status = EscrowTransaction.Status.RELEASED
            escrow.save(update_fields=["status", "updated_at"])

            dispute.status = Dispute.Status.RESOLVED
            dispute.resolution_outcome = Dispute.ResolutionOutcome.RELEASE
            dispute.resolution_notes = resolution_notes or "Resolved by admin: release funds to seller."
            dispute.save(update_fields=["status", "resolution_outcome", "resolution_notes", "updated_at"])
            log_audit_event(
                actor=request.user,
                action=AuditLog.Action.DISPUTE_RESOLVED,
                object_type="dispute",
                object_id=dispute.id,
                metadata={
                    "escrow_id": escrow.id,
                    "outcome": dispute.resolution_outcome,
                    "status": dispute.status,
                    "escrow_status": escrow.status,
                },
            )

        data = {
            "dispute": DisputeSerializer(dispute).data,
            "escrow_status": escrow.status,
            "payout": payout_payload(payout),
        }
        return Response(
            build_response(True, "Dispute resolved with release.", data=data, errors=None),
            status=status.HTTP_200_OK,
        )

    with transaction.atomic():
        dispute = (
            Dispute.objects.select_for_update()
            .select_related("escrow", "escrow__buyer", "escrow__seller")
            .filter(id=dispute_id)
            .first()
        )
        if not dispute:
            return Response(
                build_response(False, "Dispute not found.", data=None, errors={"dispute": ["Dispute does not exist."]}),
                status=status.HTTP_404_NOT_FOUND,
            )

        if dispute.status != Dispute.Status.OPEN:
            return Response(
                build_response(
                    False,
                    "Dispute resolution failed.",
                    data=None,
                    errors={"dispute": ["This dispute has already been resolved."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        escrow = EscrowTransaction.objects.select_for_update().filter(id=dispute.escrow_id).first()
        if escrow.status != EscrowTransaction.Status.DISPUTED:
            return Response(
                build_response(
                    False,
                    "Dispute resolution failed.",
                    data=None,
                    errors={"escrow": [f"Dispute resolution requires escrow in 'disputed' status, got '{escrow.status}'."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing_refund = (
            RefundRecord.objects.select_for_update()
            .filter(
                escrow=escrow,
                status__in=[
                    RefundRecord.Status.PENDING,
                    RefundRecord.Status.PROCESSING,
                    RefundRecord.Status.SUCCESS,
                ],
            )
            .first()
        )
        if existing_refund:
            return Response(
                build_response(
                    False,
                    "Dispute resolution failed.",
                    data=None,
                    errors={"refund": ["A refund has already been triggered for this escrow."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment = (
            PaymentRecord.objects.select_for_update()
            .filter(
                escrow=escrow,
                provider=PaymentRecord.Provider.PAYSTACK,
                status=PaymentRecord.Status.SUCCESS,
            )
            .order_by("-created_at")
            .first()
        )
        if not payment:
            return Response(
                build_response(
                    False,
                    "Dispute resolution failed.",
                    data=None,
                    errors={"payment": ["No successful Paystack payment found for this escrow."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        refund = create_refund_record_for_escrow(
            escrow=escrow,
            initiated_by=request.user,
            payment_reference=payment.reference,
            reason=resolution_notes or f"Dispute #{dispute.id} resolved with refund.",
        )
        payment_reference = payment.reference
        refund_id = refund.id

    try:
        paystack_response = execute_paystack_refund_for_payment(
            payment_reference=payment_reference,
            amount=refund.amount,
        )
    except RefundExecutionError as exc:
        with transaction.atomic():
            refund = RefundRecord.objects.select_for_update().filter(id=refund_id).first()
            if refund:
                metadata = refund.metadata or {}
                metadata["last_error"] = str(exc)
                refund.status = RefundRecord.Status.FAILED
                refund.metadata = metadata
                refund.save(update_fields=["status", "metadata", "updated_at"])
        return Response(
            build_response(
                False,
                "Dispute resolution failed.",
                data=None,
                errors={"provider": [str(exc)]},
            ),
            status=status.HTTP_502_BAD_GATEWAY if "Unable to reach Paystack." in str(exc) else status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    refund_data = paystack_response.get("data", {})
    mapped_status = map_paystack_refund_status(refund_data.get("status"))
    provider_reference = str(
        refund_data.get("id")
        or refund_data.get("transaction_reference")
        or refund_data.get("reference")
        or ""
    )

    with transaction.atomic():
        dispute = Dispute.objects.select_for_update().filter(id=dispute_id).first()
        escrow = EscrowTransaction.objects.select_for_update().filter(id=dispute.escrow_id).first()
        refund = RefundRecord.objects.select_for_update().filter(id=refund_id).first()

        metadata = refund.metadata or {}
        metadata["last_refund_response"] = paystack_response
        refund.metadata = metadata
        refund.status = mapped_status
        if provider_reference:
            refund.provider_reference = provider_reference

        if mapped_status == RefundRecord.Status.SUCCESS:
            refund.processed_at = timezone.now()
            escrow.status = EscrowTransaction.Status.REFUNDED
            escrow.save(update_fields=["status", "updated_at"])

            dispute.status = Dispute.Status.RESOLVED
            dispute.resolution_outcome = Dispute.ResolutionOutcome.REFUND
            dispute.resolution_notes = resolution_notes or "Resolved by admin: refund buyer."
            dispute.save(update_fields=["status", "resolution_outcome", "resolution_notes", "updated_at"])
            log_audit_event(
                actor=request.user,
                action=AuditLog.Action.DISPUTE_RESOLVED,
                object_type="dispute",
                object_id=dispute.id,
                metadata={
                    "escrow_id": escrow.id,
                    "outcome": dispute.resolution_outcome,
                    "status": dispute.status,
                    "escrow_status": escrow.status,
                    "refund_id": refund.id,
                },
            )

        refund.save()

    if mapped_status != RefundRecord.Status.SUCCESS:
        return Response(
            build_response(
                False,
                "Dispute resolution refund is not in success status yet.",
                data={"refund": refund_payload(refund)},
                errors={"refund": ["Refund is not successful yet."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    data = {
        "dispute": DisputeSerializer(dispute).data,
        "escrow_status": escrow.status,
        "refund": refund_payload(refund),
    }
    return Response(
        build_response(True, "Dispute resolved with refund.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )
