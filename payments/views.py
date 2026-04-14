import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.audit import log_audit_event
from core.models import AuditLog
from escrow.models import EscrowTransaction

from .models import PaymentRecord, PaystackWebhookEvent, PayoutRecord, SellerPayoutDetail
from .paystack import (
    PaystackInitializationError,
    PaystackPayoutError,
    PaystackVerificationError,
    create_paystack_transfer_recipient,
    initialize_paystack_transaction,
    initiate_paystack_transfer,
    verify_paystack_signature,
    verify_paystack_transaction,
)


def build_response(success, message, data=None, errors=None):
    return {
        "success": success,
        "message": message,
        "data": data,
        "errors": errors,
    }


def payment_record_payload(record):
    return {
        "id": record.id,
        "escrow_id": record.escrow_id,
        "provider": record.provider,
        "reference": record.reference,
        "amount": str(record.amount),
        "currency": record.currency,
        "status": record.status,
        "authorization_url": record.authorization_url,
        "gateway_metadata": record.gateway_metadata,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def payout_record_payload(record):
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
        "metadata": record.metadata,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def seller_payout_detail_payload(detail):
    return {
        "id": detail.id,
        "user_id": detail.user_id,
        "provider": detail.provider,
        "bank_code": detail.bank_code,
        "account_number": detail.account_number,
        "account_name": detail.account_name,
        "currency": detail.currency,
        "recipient_code": detail.recipient_code,
        "recipient_reference": detail.recipient_reference,
        "is_active": detail.is_active,
        "created_at": detail.created_at,
        "updated_at": detail.updated_at,
    }


class PaymentProcessingError(Exception):
    def __init__(self, message, code=status.HTTP_400_BAD_REQUEST, errors=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.errors = errors or {"payment": [message]}


def generate_payment_reference(escrow_id):
    return f"escrow_{escrow_id}_{uuid4().hex[:16]}"


def map_paystack_status(paystack_status):
    normalized = str(paystack_status or "").lower()
    if normalized == "success":
        return PaymentRecord.Status.SUCCESS
    if normalized in {"failed", "abandoned"}:
        return PaymentRecord.Status.FAILED
    if normalized in {"reversed", "refunded"}:
        return PaymentRecord.Status.REVERSED
    return PaymentRecord.Status.PENDING


def map_transfer_status(paystack_transfer_status):
    normalized = str(paystack_transfer_status or "").lower()
    if normalized == "success":
        return PayoutRecord.Status.SUCCESS
    if normalized in {"pending", "processing", "otp", "queued", "received"}:
        return PayoutRecord.Status.PROCESSING
    if normalized in {"failed", "reversed"}:
        return PayoutRecord.Status.FAILED
    return PayoutRecord.Status.PROCESSING


def payout_error_response(message, errors, code=status.HTTP_400_BAD_REQUEST):
    return Response(
        build_response(False, message, data=None, errors=errors),
        status=code,
    )


def mark_payout_failed(payout_id, error_message, extra_metadata=None):
    with transaction.atomic():
        payout = PayoutRecord.objects.select_for_update().filter(id=payout_id).first()
        if not payout:
            return None
        metadata = payout.metadata or {}
        metadata["last_error"] = {"message": error_message}
        if extra_metadata:
            metadata["last_error"]["details"] = extra_metadata
        payout.status = PayoutRecord.Status.FAILED
        payout.metadata = metadata
        payout.save(update_fields=["status", "metadata", "updated_at"])
        return payout


def process_verified_payment_reference(reference, actor=None, source="api_verify", webhook_payload=None):
    """Verify a Paystack reference and apply safe, idempotent escrow funding updates."""
    try:
        verify_response = verify_paystack_transaction(reference=reference)
    except PaystackVerificationError as exc:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
        if "Unable to reach Paystack." in str(exc):
            code = status.HTTP_502_BAD_GATEWAY
        raise PaymentProcessingError(
            "Payment verification failed.",
            code=code,
            errors={"provider": [str(exc)]},
        ) from exc

    verify_data = verify_response.get("data", {})
    verified_reference = str(verify_data.get("reference") or "")
    if verified_reference != str(reference):
        raise PaymentProcessingError(
            "Payment verification failed.",
            code=status.HTTP_400_BAD_REQUEST,
            errors={"reference": ["Verification reference does not match request reference."]},
        )

    with transaction.atomic():
        payment = (
            PaymentRecord.objects.select_for_update()
            .select_related("escrow", "escrow__buyer", "escrow__seller")
            .filter(reference=reference)
            .first()
        )
        if not payment:
            raise PaymentProcessingError(
                "Payment record not found.",
                code=status.HTTP_404_NOT_FOUND,
                errors={"reference": ["No payment record exists for this reference."]},
            )

        escrow = EscrowTransaction.objects.select_for_update().filter(id=payment.escrow_id).first()
        if not escrow:
            raise PaymentProcessingError(
                "Escrow not found.",
                code=status.HTTP_404_NOT_FOUND,
                errors={"escrow": ["Escrow does not exist."]},
            )
        previous_payment_status = payment.status
        previous_escrow_status = escrow.status

        verified_amount_kobo = verify_data.get("amount")
        try:
            verified_amount_kobo = int(verified_amount_kobo)
        except (TypeError, ValueError):
            raise PaymentProcessingError(
                "Payment verification failed.",
                code=status.HTTP_400_BAD_REQUEST,
                errors={"provider": ["Invalid amount returned by Paystack."]},
            )

        expected_amount_kobo = int((Decimal(escrow.amount) * Decimal("100")).quantize(Decimal("1")))
        if verified_amount_kobo != expected_amount_kobo:
            raise PaymentProcessingError(
                "Payment verification failed.",
                code=status.HTTP_400_BAD_REQUEST,
                errors={"amount": ["Verified amount does not match escrow amount."]},
            )

        verified_currency = str(verify_data.get("currency") or payment.currency or "NGN").upper()
        if payment.currency and payment.currency.upper() != verified_currency:
            raise PaymentProcessingError(
                "Payment verification failed.",
                code=status.HTTP_400_BAD_REQUEST,
                errors={"currency": ["Verified currency does not match payment currency."]},
            )

        mapped_status = map_paystack_status(verify_data.get("status"))
        metadata = payment.gateway_metadata or {}
        metadata["last_verification"] = verify_response
        if webhook_payload is not None:
            metadata["last_webhook"] = webhook_payload

        # Never downgrade a previously successful payment due to a later inconsistent verify response.
        if previous_payment_status == PaymentRecord.Status.SUCCESS and mapped_status != PaymentRecord.Status.SUCCESS:
            metadata["status_regression_blocked"] = {
                "previous_status": previous_payment_status,
                "received_status": mapped_status,
                "source": source,
            }
            mapped_status = PaymentRecord.Status.SUCCESS

        payment.provider = PaymentRecord.Provider.PAYSTACK
        payment.status = mapped_status
        payment.currency = verified_currency
        payment.gateway_metadata = metadata
        payment.save()

        if mapped_status != PaymentRecord.Status.SUCCESS:
            return payment, escrow, False, False

        if previous_payment_status != PaymentRecord.Status.SUCCESS:
            log_audit_event(
                actor=actor,
                action=AuditLog.Action.PAYMENT_VERIFIED,
                object_type="payment",
                object_id=payment.id,
                metadata={
                    "escrow_id": escrow.id,
                    "reference": payment.reference,
                    "provider": payment.provider,
                    "status": payment.status,
                    "source": source,
                },
            )

        if escrow.status == EscrowTransaction.Status.FUNDED:
            return payment, escrow, True, True

        if escrow.status not in {EscrowTransaction.Status.PENDING, EscrowTransaction.Status.PAYMENT_PENDING}:
            raise PaymentProcessingError(
                "Payment verification failed.",
                code=status.HTTP_400_BAD_REQUEST,
                errors={"escrow": [f"Escrow cannot be funded in '{escrow.status}' status."]},
            )

        escrow.status = EscrowTransaction.Status.FUNDED
        escrow.save(update_fields=["status", "updated_at"])
        log_audit_event(
            actor=actor,
            action=AuditLog.Action.ESCROW_FUNDED,
            object_type="escrow",
            object_id=escrow.id,
            metadata={
                "payment_id": payment.id,
                "payment_reference": payment.reference,
                "status_before": previous_escrow_status,
                "status_after": escrow.status,
                "source": source,
            },
        )
        return payment, escrow, True, False


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def initialize_escrow_payment_view(request):
    """Initialize Paystack checkout for a buyer-owned escrow in a payable state."""
    escrow_id = request.data.get("escrow_id")

    if escrow_id is None:
        return Response(
            build_response(
                False,
                "Payment initialization failed.",
                data=None,
                errors={"escrow_id": ["escrow_id is required."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        escrow_id = int(escrow_id)
    except (TypeError, ValueError):
        return Response(
            build_response(
                False,
                "Payment initialization failed.",
                data=None,
                errors={"escrow_id": ["escrow_id must be a valid integer."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    escrow = EscrowTransaction.objects.select_related("buyer", "seller", "listing").filter(id=escrow_id).first()
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
                errors={"permission": ["Only the escrow buyer can initialize payment."]},
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    valid_statuses = {EscrowTransaction.Status.PENDING, EscrowTransaction.Status.PAYMENT_PENDING}
    if escrow.status not in valid_statuses:
        return Response(
            build_response(
                False,
                "Payment initialization failed.",
                data=None,
                errors={"escrow": [f"Escrow cannot be paid in '{escrow.status}' status."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    reference = generate_payment_reference(escrow.id)
    amount_kobo = int((Decimal(escrow.amount) * Decimal("100")).quantize(Decimal("1")))
    currency = "NGN"

    try:
        paystack_response = initialize_paystack_transaction(
            email=escrow.buyer.email,
            amount_kobo=amount_kobo,
            reference=reference,
            currency=currency,
            metadata={
                "escrow_id": escrow.id,
                "buyer_id": escrow.buyer_id,
                "seller_id": escrow.seller_id,
            },
        )
    except PaystackInitializationError as exc:
        error_code = status.HTTP_500_INTERNAL_SERVER_ERROR
        if "Unable to reach Paystack." in str(exc):
            error_code = status.HTTP_502_BAD_GATEWAY
        return Response(
            build_response(
                False,
                "Payment initialization failed.",
                data=None,
                errors={"provider": [str(exc)]},
            ),
            status=error_code,
        )

    paystack_data = paystack_response.get("data", {})
    authorization_url = paystack_data.get("authorization_url")

    with transaction.atomic():
        locked_escrow = (
            EscrowTransaction.objects.select_for_update()
            .select_related("buyer", "seller")
            .filter(id=escrow.id)
            .first()
        )
        if not locked_escrow:
            return Response(
                build_response(False, "Escrow not found.", data=None, errors={"escrow": ["Escrow does not exist."]}),
                status=status.HTTP_404_NOT_FOUND,
            )

        if locked_escrow.status not in valid_statuses:
            return Response(
                build_response(
                    False,
                    "Payment initialization failed.",
                    data=None,
                    errors={"escrow": [f"Escrow cannot be paid in '{locked_escrow.status}' status."]},
                ),
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment_record = (
            PaymentRecord.objects.select_for_update()
            .filter(escrow=locked_escrow)
            .order_by("-created_at")
            .first()
        )

        if payment_record:
            payment_record.provider = PaymentRecord.Provider.PAYSTACK
            payment_record.reference = reference
            payment_record.amount = locked_escrow.amount
            payment_record.currency = currency
            payment_record.status = PaymentRecord.Status.INITIALIZED
            payment_record.authorization_url = authorization_url
            payment_record.gateway_metadata = paystack_data
            payment_record.save()
        else:
            payment_record = PaymentRecord.objects.create(
                escrow=locked_escrow,
                provider=PaymentRecord.Provider.PAYSTACK,
                reference=reference,
                amount=locked_escrow.amount,
                currency=currency,
                status=PaymentRecord.Status.INITIALIZED,
                authorization_url=authorization_url,
                gateway_metadata=paystack_data,
            )

        if locked_escrow.status == EscrowTransaction.Status.PENDING:
            locked_escrow.status = EscrowTransaction.Status.PAYMENT_PENDING
            locked_escrow.save(update_fields=["status", "updated_at"])
        escrow_status = locked_escrow.status

    log_audit_event(
        actor=request.user,
        action=AuditLog.Action.PAYMENT_INITIALIZED,
        object_type="payment",
        object_id=payment_record.id,
        metadata={
            "escrow_id": escrow.id,
            "reference": payment_record.reference,
            "status": payment_record.status,
            "currency": payment_record.currency,
            "amount": str(payment_record.amount),
        },
    )

    data = {
        "escrow_id": escrow.id,
        "escrow_status": escrow_status,
        "payment": payment_record_payload(payment_record),
        "checkout": {
            "authorization_url": paystack_data.get("authorization_url"),
            "access_code": paystack_data.get("access_code"),
            "reference": paystack_data.get("reference") or reference,
        },
    }
    return Response(
        build_response(True, "Payment initialized successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def verify_escrow_payment_view(request):
    """Verify a payment reference (buyer-only) and fund escrow only on verified success."""
    reference = request.data.get("reference")

    if not reference or not isinstance(reference, str):
        return Response(
            build_response(
                False,
                "Payment verification failed.",
                data=None,
                errors={"reference": ["reference is required."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    payment = PaymentRecord.objects.select_related("escrow").filter(reference=reference).first()
    if not payment:
        return Response(
            build_response(
                False,
                "Payment record not found.",
                data=None,
                errors={"reference": ["No payment record exists for this reference."]},
            ),
            status=status.HTTP_404_NOT_FOUND,
        )

    if payment.escrow.buyer_id != request.user.id:
        return Response(
            build_response(
                False,
                "Permission denied.",
                data=None,
                errors={"permission": ["Only the escrow buyer can verify this payment."]},
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        payment, escrow, is_success, already_funded = process_verified_payment_reference(
            reference=reference,
            actor=request.user,
            source="api_verify",
        )
    except PaymentProcessingError as exc:
        return Response(
            build_response(False, exc.message, data=None, errors=exc.errors),
            status=exc.code,
        )

    response_data = {
        "escrow_id": escrow.id,
        "escrow_status": escrow.status,
        "payment": payment_record_payload(payment),
    }

    if not is_success:
        return Response(
            build_response(
                False,
                "Payment is not successful yet.",
                data=response_data,
                errors={"payment": ["Payment is not in successful status."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    if already_funded:
        return Response(
            build_response(True, "Payment already verified and escrow already funded.", data=response_data, errors=None),
            status=status.HTTP_200_OK,
        )

    return Response(
        build_response(True, "Payment verified successfully and escrow funded.", data=response_data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def paystack_webhook_view(request):
    """Handle Paystack webhooks with signature checks and duplicate-event protection."""
    signature = request.headers.get("x-paystack-signature")
    raw_body = request.body or b""

    if not verify_paystack_signature(raw_body, signature):
        return Response(
            build_response(
                False,
                "Invalid Paystack signature.",
                data=None,
                errors={"signature": ["Webhook signature verification failed."]},
            ),
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return Response(
            build_response(False, "Invalid webhook payload.", data=None, errors={"payload": ["Invalid JSON payload."]}),
            status=status.HTTP_400_BAD_REQUEST,
        )

    event_type = payload.get("event")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = data.get("reference")
    event_id = data.get("id")
    payload_hash = hashlib.sha256(raw_body).hexdigest()

    duplicate_event_query = Q(payload_hash=payload_hash)
    if event_id is not None:
        duplicate_event_query |= (
            Q(event=str(event_type or ""))
            & Q(event_id=str(event_id))
            & Q(reference=str(reference) if reference else None)
        )

    existing_event = PaystackWebhookEvent.objects.filter(duplicate_event_query).first()
    if existing_event:
        return Response(
            build_response(
                True,
                "Duplicate webhook event ignored.",
                data={"duplicate": True},
                errors=None,
            ),
            status=status.HTTP_200_OK,
        )

    try:
        with transaction.atomic():
            webhook_event = PaystackWebhookEvent.objects.create(
                event=str(event_type or ""),
                event_id=str(event_id) if event_id is not None else None,
                reference=str(reference) if reference else None,
                payload_hash=payload_hash,
                payload=payload,
                processed=False,
            )
    except IntegrityError:
        return Response(
            build_response(
                True,
                "Duplicate webhook event ignored.",
                data={"duplicate": True},
                errors=None,
            ),
            status=status.HTTP_200_OK,
        )

    if event_type != "charge.success":
        webhook_event.processed = True
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=["processed", "processed_at"])
        return Response(
            build_response(
                True,
                f"Webhook event '{event_type}' received with no funding action.",
                data={"reference": reference},
                errors=None,
            ),
            status=status.HTTP_200_OK,
        )

    if not reference:
        return Response(
            build_response(
                False,
                "Payment verification failed.",
                data=None,
                errors={"reference": ["Reference is missing in webhook payload."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        payment, escrow, is_success, already_funded = process_verified_payment_reference(
            reference=reference,
            actor=None,
            source="paystack_webhook",
            webhook_payload=payload,
        )
    except PaymentProcessingError as exc:
        return Response(
            build_response(False, exc.message, data=None, errors=exc.errors),
            status=exc.code,
        )

    webhook_event.processed = True
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=["processed", "processed_at"])

    response_data = {
        "escrow_id": escrow.id,
        "escrow_status": escrow.status,
        "payment_status": payment.status,
        "reference": reference,
    }

    if not is_success:
        return Response(
            build_response(
                False,
                "Payment is not successful yet.",
                data=response_data,
                errors={"payment": ["Payment is not in successful status."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    if already_funded:
        return Response(
            build_response(True, "Duplicate payment event received; escrow already funded.", data=response_data, errors=None),
            status=status.HTTP_200_OK,
        )

    return Response(
        build_response(True, "Payment verified via webhook and escrow funded.", data=response_data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def upsert_seller_payout_detail_view(request):
    """Create or replace the authenticated seller payout destination details."""
    bank_code = request.data.get("bank_code")
    account_number = request.data.get("account_number")
    account_name = request.data.get("account_name")
    currency = request.data.get("currency", "NGN")

    errors = {}
    if not bank_code or not isinstance(bank_code, str):
        errors["bank_code"] = ["bank_code is required."]
    if not account_number or not isinstance(account_number, str):
        errors["account_number"] = ["account_number is required."]
    elif not account_number.isdigit():
        errors["account_number"] = ["account_number must contain only digits."]

    if currency is None or not isinstance(currency, str) or not currency.strip():
        errors["currency"] = ["currency must be a valid string."]

    if errors:
        return payout_error_response("Payout details update failed.", errors, status.HTTP_400_BAD_REQUEST)

    bank_code = bank_code.strip()
    account_number = account_number.strip()
    currency = currency.strip().upper()
    account_name = str(account_name).strip() if account_name else ""
    if not account_name:
        full_name = f"{request.user.first_name} {request.user.last_name}".strip()
        account_name = full_name or request.user.email

    with transaction.atomic():
        detail, created = SellerPayoutDetail.objects.select_for_update().get_or_create(
            user=request.user,
            defaults={
                "provider": SellerPayoutDetail.Provider.PAYSTACK,
                "bank_code": bank_code,
                "account_number": account_number,
                "account_name": account_name,
                "currency": currency,
                "is_active": True,
            },
        )

        if not created:
            destination_changed = detail.bank_code != bank_code or detail.account_number != account_number
            detail.provider = SellerPayoutDetail.Provider.PAYSTACK
            detail.bank_code = bank_code
            detail.account_number = account_number
            detail.account_name = account_name
            detail.currency = currency
            detail.is_active = True
            if destination_changed:
                detail.recipient_code = None
                detail.recipient_reference = None
                detail.metadata = {}
            detail.save()

    message = "Payout details created successfully." if created else "Payout details updated successfully."
    response_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return Response(
        build_response(True, message, data=seller_payout_detail_payload(detail), errors=None),
        status=response_code,
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_seller_payout_detail_view(request):
    """Partially update the authenticated seller payout destination details."""
    detail = SellerPayoutDetail.objects.filter(user=request.user).first()
    if not detail:
        return payout_error_response(
            "Payout details not found.",
            {"payout_details": ["Create payout details first."]},
            status.HTTP_404_NOT_FOUND,
        )

    bank_code = request.data.get("bank_code", None)
    account_number = request.data.get("account_number", None)
    account_name = request.data.get("account_name", None)
    currency = request.data.get("currency", None)
    is_active = request.data.get("is_active", None)

    if all(value is None for value in [bank_code, account_number, account_name, currency, is_active]):
        return payout_error_response(
            "Payout details update failed.",
            {"fields": ["Provide at least one field to update."]},
            status.HTTP_400_BAD_REQUEST,
        )

    errors = {}
    if bank_code is not None:
        if not isinstance(bank_code, str) or not bank_code.strip():
            errors["bank_code"] = ["bank_code must be a non-empty string."]
    if account_number is not None:
        if not isinstance(account_number, str) or not account_number.strip():
            errors["account_number"] = ["account_number must be a non-empty string."]
        elif not account_number.isdigit():
            errors["account_number"] = ["account_number must contain only digits."]
    if account_name is not None:
        if not isinstance(account_name, str):
            errors["account_name"] = ["account_name must be a string."]
    if currency is not None:
        if not isinstance(currency, str) or not currency.strip():
            errors["currency"] = ["currency must be a non-empty string."]
    if is_active is not None and not isinstance(is_active, bool):
        errors["is_active"] = ["is_active must be a boolean."]

    if errors:
        return payout_error_response("Payout details update failed.", errors, status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        detail = SellerPayoutDetail.objects.select_for_update().filter(id=detail.id).first()
        destination_changed = False

        if bank_code is not None:
            normalized_bank_code = bank_code.strip()
            if detail.bank_code != normalized_bank_code:
                destination_changed = True
            detail.bank_code = normalized_bank_code

        if account_number is not None:
            normalized_account_number = account_number.strip()
            if detail.account_number != normalized_account_number:
                destination_changed = True
            detail.account_number = normalized_account_number

        if account_name is not None:
            detail.account_name = account_name.strip()

        if currency is not None:
            detail.currency = currency.strip().upper()

        if is_active is not None:
            detail.is_active = is_active

        if destination_changed:
            detail.recipient_code = None
            detail.recipient_reference = None
            detail.metadata = {}

        detail.save()

    return Response(
        build_response(True, "Payout details updated successfully.", data=seller_payout_detail_payload(detail), errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def execute_payout_view(request, payout_id):
    """Execute a pending payout via Paystack for a seller-owned escrow release."""
    with transaction.atomic():
        payout = (
            PayoutRecord.objects.select_for_update()
            .select_related("escrow", "escrow__seller", "escrow__buyer")
            .filter(id=payout_id)
            .first()
        )
        if not payout:
            return payout_error_response(
                "Payout not found.",
                {"payout": ["Payout record does not exist."]},
                status.HTTP_404_NOT_FOUND,
            )

        escrow = payout.escrow
        is_seller = escrow.seller_id == request.user.id
        if not is_seller and not request.user.is_staff:
            return payout_error_response(
                "Permission denied.",
                {"permission": ["Only the escrow seller or admin can execute this payout."]},
                status.HTTP_403_FORBIDDEN,
            )

        if escrow.status == EscrowTransaction.Status.DISPUTED:
            return payout_error_response(
                "Payout blocked.",
                {"escrow": ["Disputed escrow cannot be paid out."]},
                status.HTTP_400_BAD_REQUEST,
            )

        if escrow.status not in {EscrowTransaction.Status.RELEASED, EscrowTransaction.Status.COMPLETED}:
            return payout_error_response(
                "Payout blocked.",
                {"escrow": [f"Payout cannot run in '{escrow.status}' status."]},
                status.HTTP_400_BAD_REQUEST,
            )

        if payout.status in {PayoutRecord.Status.SUCCESS, PayoutRecord.Status.PROCESSING}:
            return payout_error_response(
                "Payout blocked.",
                {"payout": ["Payout has already been processed or is in progress."]},
                status.HTTP_400_BAD_REQUEST,
            )

        if payout.status == PayoutRecord.Status.REVERSED:
            return payout_error_response(
                "Payout blocked.",
                {"payout": ["Reversed payout cannot be executed again."]},
                status.HTTP_400_BAD_REQUEST,
            )

        detail = (
            SellerPayoutDetail.objects.select_for_update()
            .filter(user_id=escrow.seller_id, is_active=True)
            .first()
        )
        if not detail or not detail.bank_code or not detail.account_number:
            return payout_error_response(
                "Payout blocked.",
                {"payout_details": ["Seller payout details are required before execution."]},
                status.HTTP_400_BAD_REQUEST,
            )

        metadata = payout.metadata or {}
        metadata["execution_requested_by"] = request.user.id
        metadata["execution_requested_at"] = timezone.now().isoformat()
        payout.status = PayoutRecord.Status.PROCESSING
        payout.metadata = metadata
        payout.save(update_fields=["status", "metadata", "updated_at"])

        detail_id = detail.id
        recipient_code = detail.recipient_code
        recipient_name = detail.account_name or escrow.seller.email
        bank_code = detail.bank_code
        account_number = detail.account_number
        currency = detail.currency or payout.currency or "NGN"
        amount_kobo = int((Decimal(payout.amount) * Decimal("100")).quantize(Decimal("1")))
        payout_reference = payout.reference
        escrow_id = escrow.id

    if not recipient_code:
        try:
            recipient_response = create_paystack_transfer_recipient(
                name=recipient_name,
                account_number=account_number,
                bank_code=bank_code,
                currency=currency,
            )
        except PaystackPayoutError as exc:
            code = status.HTTP_500_INTERNAL_SERVER_ERROR
            if "Unable to reach Paystack." in str(exc):
                code = status.HTTP_502_BAD_GATEWAY
            mark_payout_failed(payout_id, str(exc))
            return payout_error_response("Payout execution failed.", {"provider": [str(exc)]}, code)

        recipient_data = recipient_response.get("data", {})
        recipient_code = recipient_data.get("recipient_code")
        if not recipient_code:
            mark_payout_failed(payout_id, "Paystack recipient_code is missing.")
            return payout_error_response(
                "Payout execution failed.",
                {"provider": ["Paystack recipient creation did not return recipient_code."]},
                status.HTTP_502_BAD_GATEWAY,
            )

        with transaction.atomic():
            detail = SellerPayoutDetail.objects.select_for_update().filter(id=detail_id).first()
            if detail:
                detail.recipient_code = recipient_code
                detail.recipient_reference = str(recipient_data.get("id") or recipient_code)
                detail_metadata = detail.metadata or {}
                detail_metadata["last_recipient_response"] = recipient_response
                detail.metadata = detail_metadata
                detail.save(update_fields=["recipient_code", "recipient_reference", "metadata", "updated_at"])

    try:
        transfer_response = initiate_paystack_transfer(
            amount_kobo=amount_kobo,
            recipient_code=recipient_code,
            reference=payout_reference,
            reason=f"Escrow payout #{escrow_id}",
        )
    except PaystackPayoutError as exc:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
        if "Unable to reach Paystack." in str(exc):
            code = status.HTTP_502_BAD_GATEWAY
        mark_payout_failed(payout_id, str(exc))
        return payout_error_response("Payout execution failed.", {"provider": [str(exc)]}, code)

    transfer_data = transfer_response.get("data", {})
    transfer_status = map_transfer_status(transfer_data.get("status"))
    provider_reference = str(
        transfer_data.get("transfer_code")
        or transfer_data.get("id")
        or transfer_data.get("reference")
        or ""
    )

    with transaction.atomic():
        payout = (
            PayoutRecord.objects.select_for_update()
            .select_related("escrow")
            .filter(id=payout_id)
            .first()
        )
        escrow = EscrowTransaction.objects.select_for_update().filter(id=payout.escrow_id).first()

        metadata = payout.metadata or {}
        metadata["last_transfer_response"] = transfer_response
        payout.metadata = metadata
        if provider_reference:
            payout.provider_reference = provider_reference

        if transfer_status == PayoutRecord.Status.SUCCESS:
            payout.status = PayoutRecord.Status.SUCCESS
            payout.processed_at = timezone.now()
            if escrow.status == EscrowTransaction.Status.RELEASED:
                escrow.status = EscrowTransaction.Status.COMPLETED
                escrow.save(update_fields=["status", "updated_at"])
            message = "Payout executed successfully."
        elif transfer_status == PayoutRecord.Status.PROCESSING:
            payout.status = PayoutRecord.Status.PROCESSING
            message = "Payout is processing."
        else:
            payout.status = PayoutRecord.Status.FAILED
            message = "Payout execution failed."

        payout.save()
        log_audit_event(
            actor=request.user,
            action=AuditLog.Action.PAYOUT_EXECUTED,
            object_type="payout",
            object_id=payout.id,
            metadata={
                "escrow_id": payout.escrow_id,
                "reference": payout.reference,
                "provider_reference": payout.provider_reference,
                "status": payout.status,
                "escrow_status": escrow.status,
            },
        )

    response_data = {
        "escrow_id": payout.escrow_id,
        "escrow_status": escrow.status,
        "payout": payout_record_payload(payout),
    }

    if payout.status == PayoutRecord.Status.FAILED:
        return Response(
            build_response(False, message, data=response_data, errors={"payout": ["Transfer failed at provider."]}),
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        build_response(True, message, data=response_data, errors=None),
        status=status.HTTP_200_OK,
    )
