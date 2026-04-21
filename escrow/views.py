from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.audit import log_audit_event
from core.models import AuditLog
from listings.models import Listing
from payments.models import PaymentRecord, PayoutRecord, RefundRecord
from payments.payouts import create_payout_record_for_release
from payments.refunds import (
    RefundExecutionError,
    create_refund_record_for_escrow,
    execute_paystack_refund_for_payment,
    map_paystack_refund_status,
)

from .models import EscrowTransaction
from .serializers import EscrowTransactionSerializer


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
        "amount": str(record.amount),
        "currency": record.currency,
        "status": record.status,
        "initiated_by": record.initiated_by_id,
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
def create_escrow_from_listing_view(request):
    """Create an escrow from an active listing using immutable listing snapshots."""
    listing_id = request.data.get("listing_id")

    if listing_id is None:
        return Response(
            build_response(
                False,
                "Escrow creation failed.",
                data=None,
                errors={"listing_id": ["listing_id is required."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        listing_id = int(listing_id)
    except (TypeError, ValueError):
        return Response(
            build_response(
                False,
                "Escrow creation failed.",
                data=None,
                errors={"listing_id": ["listing_id must be a valid integer."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    listing = Listing.objects.filter(id=listing_id).select_related("seller").first()
    if not listing:
        return Response(
            build_response(
                False,
                "Listing not found.",
                data=None,
                errors={"listing": ["Listing does not exist."]},
            ),
            status=status.HTTP_404_NOT_FOUND,
        )

    if not listing.is_active:
        return Response(
            build_response(
                False,
                "Escrow creation failed.",
                data=None,
                errors={"listing": ["Only active listings can be used to create escrow."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    if listing.seller_id == request.user.id:
        return Response(
            build_response(
                False,
                "Escrow creation failed.",
                data=None,
                errors={"buyer": ["You cannot create escrow for your own listing."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    duplicate_escrow = EscrowTransaction.objects.filter(
        listing_id=listing.id,
        buyer_id=request.user.id,
    ).exists()
    if duplicate_escrow:
        return Response(
            build_response(
                False,
                "Escrow creation failed.",
                data=None,
                errors={"listing": ["You already created an escrow for this listing."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    escrow = EscrowTransaction.objects.create(
        listing=listing,
        buyer=request.user,
        seller=listing.seller,
        amount=listing.price,
        title_snapshot=listing.title,
        description_snapshot=listing.description,
        status=EscrowTransaction.Status.PENDING,
    )
    log_audit_event(
        actor=request.user,
        action=AuditLog.Action.ESCROW_CREATED,
        object_type="escrow",
        object_id=escrow.id,
        metadata={
            "listing_id": listing.id,
            "buyer_id": escrow.buyer_id,
            "seller_id": escrow.seller_id,
            "amount": str(escrow.amount),
            "status": escrow.status,
        },
    )

    data = EscrowTransactionSerializer(escrow).data
    return Response(
        build_response(True, "Escrow created successfully.", data=data, errors=None),
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_my_escrows_view(request):
    """Authenticated endpoint for the current buyer's escrow transactions."""
    escrows = (
        EscrowTransaction.objects.filter(buyer=request.user)
        .select_related("listing", "buyer", "seller")
    )
    data = EscrowTransactionSerializer(escrows, many=True).data
    return Response(
        build_response(True, "My escrows fetched successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def buyer_escrow_detail_view(request, escrow_id):
    """Authenticated endpoint for one buyer-owned escrow transaction."""
    escrow = (
        EscrowTransaction.objects.filter(id=escrow_id, buyer=request.user)
        .select_related("listing", "buyer", "seller")
        .first()
    )
    if not escrow:
        return Response(
            build_response(False, "Escrow not found.", data=None, errors={"escrow": ["Escrow does not exist."]}),
            status=status.HTTP_404_NOT_FOUND,
        )

    data = EscrowTransactionSerializer(escrow).data
    return Response(
        build_response(True, "Escrow detail fetched successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_seller_escrows_view(request):
    """Authenticated endpoint for the current seller's escrow transactions."""
    escrows = (
        EscrowTransaction.objects.filter(seller=request.user)
        .select_related("listing", "buyer", "seller")
    )
    data = EscrowTransactionSerializer(escrows, many=True).data
    return Response(
        build_response(True, "Seller escrows fetched successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def seller_escrow_detail_view(request, escrow_id):
    """Authenticated endpoint for one seller-owned escrow transaction."""
    escrow = (
        EscrowTransaction.objects.filter(id=escrow_id, seller=request.user)
        .select_related("listing", "buyer", "seller")
        .first()
    )
    if not escrow:
        return Response(
            build_response(False, "Escrow not found.", data=None, errors={"escrow": ["Escrow does not exist."]}),
            status=status.HTTP_404_NOT_FOUND,
        )

    data = EscrowTransactionSerializer(escrow).data
    return Response(
        build_response(True, "Escrow detail fetched successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def release_escrow_view(request, escrow_id):
    """Buyer-triggered release for funded escrows; creates payout intent once."""
    with transaction.atomic():
        escrow = (
            EscrowTransaction.objects.select_for_update()
            .select_related("buyer", "seller", "listing")
            .filter(id=escrow_id)
            .first()
        )
        if not escrow:
            return Response(
                build_response(False, "Escrow not found.", data=None, errors={"escrow": ["Escrow does not exist."]}),
                status=status.HTTP_404_NOT_FOUND,
            )

        if escrow.buyer_id != request.user.id:
            return Response(
                build_response(
                    False,
                    "Permission denied.",
                    data=None,
                    errors={"permission": ["Only the escrow buyer can release this escrow."]},
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        if escrow.status == EscrowTransaction.Status.DISPUTED:
            return Response(
                build_response(
                    False,
                    "Escrow release blocked.",
                    data=None,
                    errors={"escrow": ["Disputed escrow cannot be released."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        if escrow.status in {EscrowTransaction.Status.RELEASED, EscrowTransaction.Status.COMPLETED}:
            return Response(
                build_response(
                    False,
                    "Escrow release blocked.",
                    data=None,
                    errors={"escrow": ["Escrow has already been released."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        if escrow.status != EscrowTransaction.Status.FUNDED:
            return Response(
                build_response(
                    False,
                    "Escrow release blocked.",
                    data=None,
                    errors={"escrow": [f"Escrow cannot be released in '{escrow.status}' status."]},
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
            .order_by("-created_at")
            .first()
        )
        if existing_payout:
            return Response(
                build_response(
                    False,
                    "Escrow release blocked.",
                    data=None,
                    errors={"escrow": ["Release has already been triggered for this escrow."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        payout_record = create_payout_record_for_release(
            escrow=escrow,
            initiated_by=request.user,
            currency="NGN",
            metadata={
                "trigger": "buyer_release",
                "execution": "pending_paystack_transfer",
                "escrow_status_before_release": escrow.status,
            },
        )

        escrow.status = EscrowTransaction.Status.RELEASED
        escrow.save(update_fields=["status", "updated_at"])

    data = {
        "escrow_id": escrow.id,
        "escrow_status": escrow.status,
        "payout": payout_payload(payout_record),
    }
    return Response(
        build_response(True, "Escrow released successfully. Payout queued.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def refund_escrow_view(request, escrow_id):
    """Trigger refund flow for funded escrow after strict ownership/state checks."""
    reason = request.data.get("reason", "")
    if reason is not None and not isinstance(reason, str):
        return Response(
            build_response(
                False,
                "Refund request failed.",
                data=None,
                errors={"reason": ["reason must be a string."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    reason = (reason or "").strip()

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

        if escrow.buyer_id != request.user.id and not request.user.is_staff:
            return Response(
                build_response(
                    False,
                    "Permission denied.",
                    data=None,
                    errors={"permission": ["Only the escrow buyer or admin can trigger refund at this stage."]},
                ),
                status=status.HTTP_403_FORBIDDEN,
            )

        if escrow.status == EscrowTransaction.Status.DISPUTED:
            return Response(
                build_response(
                    False,
                    "Refund blocked.",
                    data=None,
                    errors={"escrow": ["Disputed escrow cannot be refunded."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        if escrow.status in {EscrowTransaction.Status.RELEASED, EscrowTransaction.Status.COMPLETED}:
            return Response(
                build_response(
                    False,
                    "Refund blocked.",
                    data=None,
                    errors={"escrow": ["Released escrow cannot be refunded."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        if escrow.status == EscrowTransaction.Status.REFUNDED:
            return Response(
                build_response(
                    False,
                    "Refund blocked.",
                    data=None,
                    errors={"escrow": ["Escrow has already been refunded."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        if escrow.status != EscrowTransaction.Status.FUNDED:
            return Response(
                build_response(
                    False,
                    "Refund blocked.",
                    data=None,
                    errors={"escrow": [f"Escrow cannot be refunded in '{escrow.status}' status."]},
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
            .order_by("-created_at")
            .first()
        )
        if existing_refund:
            return Response(
                build_response(
                    False,
                    "Refund blocked.",
                    data=None,
                    errors={"refund": ["Refund has already been triggered for this escrow."]},
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
                    "Refund blocked.",
                    data=None,
                    errors={"payment": ["No successful Paystack payment found for this escrow."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        refund_record = create_refund_record_for_escrow(
            escrow=escrow,
            initiated_by=request.user,
            payment_reference=payment.reference,
            reason=reason,
        )

        payment_reference = payment.reference
        refund_id = refund_record.id

    try:
        paystack_response = execute_paystack_refund_for_payment(
            payment_reference=payment_reference,
            amount=refund_record.amount,
        )
    except RefundExecutionError as exc:
        with transaction.atomic():
            refund_record = RefundRecord.objects.select_for_update().filter(id=refund_id).first()
            if refund_record:
                metadata = refund_record.metadata or {}
                metadata["last_error"] = str(exc)
                refund_record.status = RefundRecord.Status.FAILED
                refund_record.metadata = metadata
                refund_record.save(update_fields=["status", "metadata", "updated_at"])
        return Response(
            build_response(
                False,
                "Refund execution failed.",
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
        escrow = EscrowTransaction.objects.select_for_update().filter(id=escrow_id).first()
        refund_record = RefundRecord.objects.select_for_update().filter(id=refund_id).first()

        metadata = refund_record.metadata or {}
        metadata["last_refund_response"] = paystack_response
        refund_record.metadata = metadata
        if provider_reference:
            refund_record.provider_reference = provider_reference
        refund_record.status = mapped_status
        if mapped_status == RefundRecord.Status.SUCCESS:
            refund_record.processed_at = timezone.now()
            escrow.status = EscrowTransaction.Status.REFUNDED
            escrow.save(update_fields=["status", "updated_at"])
        refund_record.save()

    response_data = {
        "escrow_id": escrow.id,
        "escrow_status": escrow.status,
        "refund": refund_payload(refund_record),
    }

    if mapped_status != RefundRecord.Status.SUCCESS:
        return Response(
            build_response(
                False,
                "Refund is being processed but not completed yet.",
                data=response_data,
                errors={"refund": ["Refund not in success status yet."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        build_response(True, "Refund completed successfully.", data=response_data, errors=None),
        status=status.HTTP_200_OK,
    )
