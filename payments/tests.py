import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import AuditLog
from escrow.models import EscrowTransaction
from listings.models import Listing

from .models import PaymentRecord, PaystackWebhookEvent, PayoutRecord, RefundRecord, SellerPayoutDetail
from .paystack import PaystackPayoutError


User = get_user_model()


class PaymentInitializationTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller@example.com",
            password="StrongPass123!",
            first_name="Seller",
            last_name="One",
        )
        self.buyer = User.objects.create_user(
            email="buyer@example.com",
            password="StrongPass123!",
            first_name="Buyer",
            last_name="One",
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="StrongPass123!",
            first_name="Other",
            last_name="User",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="MacBook Pro",
            description="M2 Pro chip",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("2500.00"),
            is_active=True,
        )
        self.pending_escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.PENDING,
        )
        self.funded_escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.FUNDED,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    @patch("payments.views.initialize_paystack_transaction")
    def test_successful_initialization(self, mock_initialize):
        mock_initialize.return_value = {
            "status": True,
            "message": "Authorization URL created",
            "data": {
                "authorization_url": "https://checkout.paystack.com/abc123",
                "access_code": "abc123",
                "reference": "paystack_ref_1",
            },
        }

        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("initialize-escrow-payment"),
            {"escrow_id": self.pending_escrow.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(PaymentRecord.objects.count(), 1)

        record = PaymentRecord.objects.first()
        self.assertEqual(record.escrow_id, self.pending_escrow.id)
        self.assertEqual(record.provider, PaymentRecord.Provider.PAYSTACK)
        self.assertEqual(record.amount, self.pending_escrow.amount)
        self.assertEqual(record.currency, "NGN")
        self.assertEqual(record.status, PaymentRecord.Status.INITIALIZED)
        self.assertEqual(record.authorization_url, "https://checkout.paystack.com/abc123")

        self.pending_escrow.refresh_from_db()
        self.assertEqual(self.pending_escrow.status, EscrowTransaction.Status.PAYMENT_PENDING)
        mock_initialize.assert_called_once()
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.PAYMENT_INITIALIZED,
                object_type="payment",
                object_id=record.id,
            ).exists()
        )

    @patch("payments.views.initialize_paystack_transaction")
    def test_non_buyer_blocked(self, mock_initialize):
        self.authenticate(self.other_user)
        response = self.client.post(
            reverse("initialize-escrow-payment"),
            {"escrow_id": self.pending_escrow.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.assertEqual(PaymentRecord.objects.count(), 0)
        mock_initialize.assert_not_called()

    @patch("payments.views.initialize_paystack_transaction")
    def test_invalid_escrow_state_blocked(self, mock_initialize):
        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("initialize-escrow-payment"),
            {"escrow_id": self.funded_escrow.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(PaymentRecord.objects.count(), 0)
        mock_initialize.assert_not_called()


@override_settings(PAYSTACK_SECRET_KEY="sk_test_webhook_secret")
class PaymentVerificationTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller_verify@example.com",
            password="StrongPass123!",
            first_name="Seller",
            last_name="Verify",
        )
        self.buyer = User.objects.create_user(
            email="buyer_verify@example.com",
            password="StrongPass123!",
            first_name="Buyer",
            last_name="Verify",
        )
        self.other_user = User.objects.create_user(
            email="other_verify@example.com",
            password="StrongPass123!",
            first_name="Other",
            last_name="Verify",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Gaming Laptop",
            description="RTX and 32GB RAM",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("1800.00"),
            is_active=True,
        )
        self.escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.PAYMENT_PENDING,
        )
        self.payment = PaymentRecord.objects.create(
            escrow=self.escrow,
            provider=PaymentRecord.Provider.PAYSTACK,
            reference="ps_ref_verify_001",
            amount=self.escrow.amount,
            currency="NGN",
            status=PaymentRecord.Status.INITIALIZED,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def build_paystack_verify_response(self, reference, amount_kobo, payment_status="success"):
        return {
            "status": True,
            "message": "Verification successful",
            "data": {
                "reference": reference,
                "status": payment_status,
                "amount": amount_kobo,
                "currency": "NGN",
            },
        }

    @patch("payments.views.verify_paystack_transaction")
    def test_successful_verification_marks_escrow_funded(self, mock_verify):
        mock_verify.return_value = self.build_paystack_verify_response(
            reference=self.payment.reference,
            amount_kobo=180000,
            payment_status="success",
        )
        self.authenticate(self.buyer)

        response = self.client.post(
            reverse("verify-escrow-payment"),
            {"reference": self.payment.reference},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.payment.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentRecord.Status.SUCCESS)
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.FUNDED)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.PAYMENT_VERIFIED,
                object_type="payment",
                object_id=self.payment.id,
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ESCROW_FUNDED,
                object_type="escrow",
                object_id=self.escrow.id,
            ).exists()
        )

    @patch("payments.views.verify_paystack_transaction")
    def test_duplicate_webhook_event_does_not_double_process(self, mock_verify):
        mock_verify.return_value = self.build_paystack_verify_response(
            reference=self.payment.reference,
            amount_kobo=180000,
            payment_status="success",
        )

        payload = {
            "event": "charge.success",
            "data": {
                "id": 987654321,
                "reference": self.payment.reference,
            },
        }
        raw_body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(
            key=b"sk_test_webhook_secret",
            msg=raw_body,
            digestmod=hashlib.sha512,
        ).hexdigest()

        first = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )
        second = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data["success"])
        self.assertEqual(second.data["message"], "Duplicate webhook event ignored.")

        self.payment.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentRecord.Status.SUCCESS)
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.FUNDED)
        self.assertEqual(PaystackWebhookEvent.objects.count(), 1)
        mock_verify.assert_called_once()

    def test_invalid_signature_rejected(self):
        payload = {
            "event": "charge.success",
            "data": {"id": 1, "reference": self.payment.reference},
        }
        raw_body = json.dumps(payload).encode("utf-8")

        response = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE="invalid-signature",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data["success"])

    def test_invalid_reference_rejected(self):
        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("verify-escrow-payment"),
            {"reference": "unknown_ref_123"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data["success"])

    @patch("payments.views.verify_paystack_transaction")
    def test_already_funded_escrow_protected_from_duplicate_funding(self, mock_verify):
        self.escrow.status = EscrowTransaction.Status.FUNDED
        self.escrow.save(update_fields=["status", "updated_at"])
        self.payment.status = PaymentRecord.Status.SUCCESS
        self.payment.save(update_fields=["status", "updated_at"])

        mock_verify.return_value = self.build_paystack_verify_response(
            reference=self.payment.reference,
            amount_kobo=180000,
            payment_status="success",
        )

        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("verify-escrow-payment"),
            {"reference": self.payment.reference},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(
            response.data["message"],
            "Payment already verified and escrow already funded.",
        )
        self.escrow.refresh_from_db()
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.FUNDED)

    @patch("payments.views.verify_paystack_transaction")
    def test_verified_payment_status_does_not_regress_to_failed(self, mock_verify):
        self.escrow.status = EscrowTransaction.Status.FUNDED
        self.escrow.save(update_fields=["status", "updated_at"])
        self.payment.status = PaymentRecord.Status.SUCCESS
        self.payment.save(update_fields=["status", "updated_at"])

        mock_verify.return_value = self.build_paystack_verify_response(
            reference=self.payment.reference,
            amount_kobo=180000,
            payment_status="failed",
        )

        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("verify-escrow-payment"),
            {"reference": self.payment.reference},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.payment.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.payment.status, PaymentRecord.Status.SUCCESS)
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.FUNDED)

    @patch("payments.views.verify_paystack_transaction")
    def test_duplicate_webhook_event_id_is_ignored_even_with_different_payload_hash(self, mock_verify):
        mock_verify.return_value = self.build_paystack_verify_response(
            reference=self.payment.reference,
            amount_kobo=180000,
            payment_status="success",
        )

        payload_one = {
            "event": "charge.success",
            "data": {
                "id": 987654322,
                "reference": self.payment.reference,
            },
        }
        payload_two = {
            "event": "charge.success",
            "data": {
                "id": 987654322,
                "reference": self.payment.reference,
                "channel": "card",
            },
        }

        raw_body_one = json.dumps(payload_one).encode("utf-8")
        signature_one = hmac.new(
            key=b"sk_test_webhook_secret",
            msg=raw_body_one,
            digestmod=hashlib.sha512,
        ).hexdigest()

        raw_body_two = json.dumps(payload_two).encode("utf-8")
        signature_two = hmac.new(
            key=b"sk_test_webhook_secret",
            msg=raw_body_two,
            digestmod=hashlib.sha512,
        ).hexdigest()

        first = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body_one,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature_one,
        )
        second = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body_two,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature_two,
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data["success"])
        self.assertEqual(second.data["message"], "Duplicate webhook event ignored.")
        self.assertEqual(PaystackWebhookEvent.objects.count(), 1)
        mock_verify.assert_called_once()


@override_settings(PAYSTACK_SECRET_KEY="sk_test_webhook_secret")
class RefundWebhookUpdateTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller_refund_webhook@example.com",
            password="StrongPass123!",
            first_name="Seller",
            last_name="RefundWebhook",
        )
        self.buyer = User.objects.create_user(
            email="buyer_refund_webhook@example.com",
            password="StrongPass123!",
            first_name="Buyer",
            last_name="RefundWebhook",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Refund Webhook Listing",
            description="Webhook-driven refund status update",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("2100.00"),
            is_active=True,
        )
        self.escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.FUNDED,
        )
        self.payment = PaymentRecord.objects.create(
            escrow=self.escrow,
            provider=PaymentRecord.Provider.PAYSTACK,
            reference="pay_ref_refund_webhook_001",
            amount=self.escrow.amount,
            currency="NGN",
            status=PaymentRecord.Status.SUCCESS,
        )
        self.refund = RefundRecord.objects.create(
            escrow=self.escrow,
            reference="refund_local_ref_001",
            provider_reference="778899",
            amount=self.escrow.amount,
            currency="NGN",
            status=RefundRecord.Status.PROCESSING,
            initiated_by=self.buyer,
            metadata={"payment_reference": self.payment.reference},
        )

    def sign_payload(self, payload):
        raw_body = json.dumps(payload).encode("utf-8")
        signature = hmac.new(
            key=b"sk_test_webhook_secret",
            msg=raw_body,
            digestmod=hashlib.sha512,
        ).hexdigest()
        return raw_body, signature

    def test_refund_processed_webhook_marks_refund_success(self):
        payload = {
            "event": "refund.processed",
            "data": {
                "id": 778899,
                "status": "processed",
                "transaction_reference": self.payment.reference,
                "refund_reference": self.refund.reference,
            },
        }
        raw_body, signature = self.sign_payload(payload)

        response = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.refund.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRecord.Status.SUCCESS)
        self.assertIsNotNone(self.refund.processed_at)
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.REFUNDED)
        self.assertEqual(PaystackWebhookEvent.objects.count(), 1)

    def test_refund_processing_webhook_keeps_refund_not_success(self):
        self.refund.status = RefundRecord.Status.PENDING
        self.refund.save(update_fields=["status", "updated_at"])

        payload = {
            "event": "refund.processing",
            "data": {
                "id": 778899,
                "status": "processing",
                "transaction_reference": self.payment.reference,
            },
        }
        raw_body, signature = self.sign_payload(payload)

        response = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.refund.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.refund.status, RefundRecord.Status.PROCESSING)
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.FUNDED)

    def test_refund_webhook_without_matching_record_returns_safe_200(self):
        payload = {
            "event": "refund.processed",
            "data": {
                "id": 1234567,
                "status": "processed",
                "transaction_reference": "unknown_payment_reference",
            },
        }
        raw_body, signature = self.sign_payload(payload)

        response = self.client.post(
            reverse("paystack-webhook"),
            data=raw_body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["message"], "Refund webhook received with no matching refund record.")


@override_settings(PAYSTACK_SECRET_KEY="sk_test_payout_secret")
class PaystackPayoutIntegrationTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller_payout@example.com",
            password="StrongPass123!",
            first_name="Payout",
            last_name="Seller",
        )
        self.buyer = User.objects.create_user(
            email="buyer_payout@example.com",
            password="StrongPass123!",
            first_name="Payout",
            last_name="Buyer",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Payout Listing",
            description="Ready for payout flow",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("1500.00"),
            is_active=True,
        )
        self.escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.FUNDED,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def create_payout_request(self):
        self.authenticate(self.buyer)
        response = self.client.post(reverse("release-escrow", kwargs={"escrow_id": self.escrow.id}), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials()
        return PayoutRecord.objects.get(escrow=self.escrow)

    def add_seller_payout_details(self):
        self.authenticate(self.seller)
        response = self.client.post(
            reverse("upsert-seller-payout-detail"),
            {
                "bank_code": "058",
                "account_number": "0123456789",
                "account_name": "Payout Seller",
                "currency": "NGN",
            },
            format="json",
        )
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_200_OK])
        self.client.credentials()

    def test_payout_request_creation(self):
        payout = self.create_payout_request()
        self.escrow.refresh_from_db()

        self.assertEqual(self.escrow.status, EscrowTransaction.Status.RELEASED)
        self.assertEqual(payout.status, PayoutRecord.Status.PENDING)
        self.assertEqual(payout.amount, self.escrow.amount)

    @patch("payments.views.initiate_paystack_transfer")
    @patch("payments.views.create_paystack_transfer_recipient")
    @patch("payments.views.resolve_paystack_account")
    def test_payout_execution_success_path(self, mock_resolve_account, mock_create_recipient, mock_transfer):
        payout = self.create_payout_request()
        self.add_seller_payout_details()

        mock_resolve_account.return_value = {
            "status": True,
            "data": {
                "account_name": "Payout Seller",
            },
        }
        mock_create_recipient.return_value = {
            "status": True,
            "data": {
                "id": 3456,
                "recipient_code": "RCP_test_123",
            },
        }
        mock_transfer.return_value = {
            "status": True,
            "data": {
                "id": 7890,
                "transfer_code": "TRF_test_456",
                "reference": payout.reference,
                "status": "success",
            },
        }

        self.authenticate(self.seller)
        response = self.client.post(reverse("execute-payout", kwargs={"payout_id": payout.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        payout.refresh_from_db()
        self.escrow.refresh_from_db()
        detail = SellerPayoutDetail.objects.get(user=self.seller)

        self.assertEqual(payout.status, PayoutRecord.Status.SUCCESS)
        self.assertEqual(payout.provider_reference, "TRF_test_456")
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.COMPLETED)
        self.assertEqual(detail.recipient_code, "RCP_test_123")
        mock_resolve_account.assert_called_once()
        mock_create_recipient.assert_called_once()
        mock_transfer.assert_called_once()
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.PAYOUT_EXECUTED,
                object_type="payout",
                object_id=payout.id,
            ).exists()
        )

    @patch("payments.views.initiate_paystack_transfer")
    @patch("payments.views.create_paystack_transfer_recipient")
    @patch("payments.views.resolve_paystack_account")
    def test_duplicate_payout_prevention(self, mock_resolve_account, mock_create_recipient, mock_transfer):
        payout = self.create_payout_request()
        self.add_seller_payout_details()

        mock_resolve_account.return_value = {
            "status": True,
            "data": {
                "account_name": "Payout Seller",
            },
        }
        mock_create_recipient.return_value = {
            "status": True,
            "data": {
                "id": 111,
                "recipient_code": "RCP_test_dup",
            },
        }
        mock_transfer.return_value = {
            "status": True,
            "data": {
                "id": 222,
                "transfer_code": "TRF_test_dup",
                "reference": payout.reference,
                "status": "success",
            },
        }

        self.authenticate(self.seller)
        first = self.client.post(reverse("execute-payout", kwargs={"payout_id": payout.id}), {}, format="json")
        second = self.client.post(reverse("execute-payout", kwargs={"payout_id": payout.id}), {}, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(second.data["success"])
        mock_resolve_account.assert_called_once()
        mock_transfer.assert_called_once()

    @patch("payments.views.initiate_paystack_transfer")
    @patch("payments.views.create_paystack_transfer_recipient")
    @patch("payments.views.resolve_paystack_account")
    def test_account_resolution_failure_returns_400(self, mock_resolve_account, mock_create_recipient, mock_transfer):
        payout = self.create_payout_request()
        self.add_seller_payout_details()
        mock_resolve_account.side_effect = PaystackPayoutError(
            "Cannot resolve account",
            payload={"message": "Cannot resolve account"},
            status_code=400,
        )

        self.authenticate(self.seller)
        response = self.client.post(reverse("execute-payout", kwargs={"payout_id": payout.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("payout_details", response.data["errors"])
        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutRecord.Status.FAILED)
        self.assertEqual((payout.metadata or {}).get("last_error", {}).get("message"), "Cannot resolve account")
        self.assertEqual((payout.metadata or {}).get("last_error", {}).get("details", {}).get("stage"), "account_resolve")
        mock_create_recipient.assert_not_called()
        mock_transfer.assert_not_called()

    @patch("payments.views.initiate_paystack_transfer")
    @patch("payments.views.create_paystack_transfer_recipient")
    @patch("payments.views.resolve_paystack_account")
    def test_account_resolution_network_failure_returns_502(self, mock_resolve_account, mock_create_recipient, mock_transfer):
        payout = self.create_payout_request()
        self.add_seller_payout_details()
        mock_resolve_account.side_effect = PaystackPayoutError("Unable to reach Paystack.", status_code=502)

        self.authenticate(self.seller)
        response = self.client.post(reverse("execute-payout", kwargs={"payout_id": payout.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertFalse(response.data["success"])
        self.assertIn("provider", response.data["errors"])
        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutRecord.Status.FAILED)
        mock_create_recipient.assert_not_called()
        mock_transfer.assert_not_called()

    def test_missing_payout_details_blocked(self):
        payout = self.create_payout_request()

        self.authenticate(self.seller)
        response = self.client.post(reverse("execute-payout", kwargs={"payout_id": payout.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutRecord.Status.PENDING)

    def test_seller_can_update_payout_details(self):
        self.add_seller_payout_details()
        detail = SellerPayoutDetail.objects.get(user=self.seller)
        detail.recipient_code = "RCP_existing"
        detail.recipient_reference = "12345"
        detail.save(update_fields=["recipient_code", "recipient_reference", "updated_at"])

        self.authenticate(self.seller)
        response = self.client.patch(
            reverse("update-seller-payout-detail"),
            {
                "bank_code": "033",
                "account_number": "9998887776",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        detail.refresh_from_db()
        self.assertEqual(detail.bank_code, "033")
        self.assertEqual(detail.account_number, "9998887776")
        self.assertIsNone(detail.recipient_code)
        self.assertIsNone(detail.recipient_reference)


class SellerPayoutReadTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="seller_read_payouts@example.com",
            password="StrongPass123!",
            first_name="Seller",
            last_name="Reader",
        )
        self.other_seller = User.objects.create_user(
            email="other_seller_read_payouts@example.com",
            password="StrongPass123!",
            first_name="Other",
            last_name="Seller",
        )
        self.buyer = User.objects.create_user(
            email="buyer_read_payouts@example.com",
            password="StrongPass123!",
            first_name="Buyer",
            last_name="Reader",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Seller Payout Read Listing",
            description="Seller payout read test listing",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("1200.00"),
            is_active=True,
        )
        self.other_listing = Listing.objects.create(
            seller=self.other_seller,
            title="Other Seller Payout Listing",
            description="Other seller payout read listing",
            listing_type=Listing.ListingType.SERVICE,
            price=Decimal("900.00"),
            is_active=True,
        )
        self.escrow_one = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=Decimal("1200.00"),
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.RELEASED,
        )
        self.escrow_two = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=Decimal("800.00"),
            title_snapshot="Second Payout Escrow",
            description_snapshot="Second payout escrow snapshot",
            status=EscrowTransaction.Status.COMPLETED,
        )
        self.other_escrow = EscrowTransaction.objects.create(
            listing=self.other_listing,
            buyer=self.buyer,
            seller=self.other_seller,
            amount=Decimal("900.00"),
            title_snapshot=self.other_listing.title,
            description_snapshot=self.other_listing.description,
            status=EscrowTransaction.Status.RELEASED,
        )
        self.payout_one = PayoutRecord.objects.create(
            escrow=self.escrow_one,
            reference="payout_read_001",
            amount=Decimal("1200.00"),
            currency="NGN",
            status=PayoutRecord.Status.PENDING,
            initiated_by=self.buyer,
        )
        self.payout_two = PayoutRecord.objects.create(
            escrow=self.escrow_two,
            reference="payout_read_002",
            amount=Decimal("800.00"),
            currency="NGN",
            status=PayoutRecord.Status.SUCCESS,
            initiated_by=self.buyer,
        )
        self.other_payout = PayoutRecord.objects.create(
            escrow=self.other_escrow,
            reference="payout_read_003",
            amount=Decimal("900.00"),
            currency="NGN",
            status=PayoutRecord.Status.PROCESSING,
            initiated_by=self.buyer,
        )
        SellerPayoutDetail.objects.create(
            user=self.seller,
            bank_code="058",
            account_number="0123456789",
            account_name="Seller Reader",
            currency="NGN",
            is_active=True,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_get_my_payout_details_returns_payload_when_record_exists(self):
        self.authenticate(self.seller)
        response = self.client.get(reverse("upsert-seller-payout-detail"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["bank_code"], "058")
        self.assertEqual(response.data["data"]["account_number"], "0123456789")

    def test_get_my_payout_details_returns_empty_object_when_missing(self):
        self.authenticate(self.other_seller)
        response = self.client.get(reverse("upsert-seller-payout-detail"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"], {})

    def test_get_my_payout_details_requires_authentication(self):
        response = self.client.get(reverse("upsert-seller-payout-detail"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_my_payouts_returns_only_authenticated_seller_records(self):
        self.authenticate(self.seller)
        response = self.client.get(reverse("list-my-payouts"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(len(response.data["data"]), 2)
        returned_ids = {item["id"] for item in response.data["data"]}
        self.assertSetEqual(returned_ids, {self.payout_one.id, self.payout_two.id})
        self.assertNotIn(self.other_payout.id, returned_ids)

    def test_my_payout_detail_returns_seller_owned_record(self):
        self.authenticate(self.seller)
        response = self.client.get(reverse("my-payout-detail", kwargs={"payout_id": self.payout_one.id}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["id"], self.payout_one.id)
        self.assertEqual(response.data["data"]["escrow"]["id"], self.escrow_one.id)
        self.assertEqual(response.data["data"]["destination"]["account_number"], "0123456789")

    def test_my_payout_detail_for_other_seller_returns_not_found(self):
        self.authenticate(self.seller)
        response = self.client.get(reverse("my-payout-detail", kwargs={"payout_id": self.other_payout.id}))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data["success"])
        self.assertIn("payout", response.data["errors"])
