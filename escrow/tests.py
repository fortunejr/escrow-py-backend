from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import AuditLog
from listings.models import Listing
from payments.models import PaymentRecord, PayoutRecord, RefundRecord

from .models import EscrowTransaction


User = get_user_model()


class CreateEscrowFromListingTests(APITestCase):
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
        self.active_listing = Listing.objects.create(
            seller=self.seller,
            title="iPhone 15",
            description="Factory unlocked and clean.",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("1200.00"),
            is_active=True,
        )
        self.inactive_listing = Listing.objects.create(
            seller=self.seller,
            title="Private Listing",
            description="Not open anymore.",
            listing_type=Listing.ListingType.SERVICE,
            price=Decimal("500.00"),
            is_active=False,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_successful_creation(self):
        self.authenticate(self.buyer)
        payload = {"listing_id": self.active_listing.id}

        response = self.client.post(reverse("create-escrow-from-listing"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(EscrowTransaction.objects.count(), 1)

        escrow = EscrowTransaction.objects.first()
        self.assertEqual(escrow.listing_id, self.active_listing.id)
        self.assertEqual(escrow.buyer_id, self.buyer.id)
        self.assertEqual(escrow.seller_id, self.seller.id)
        self.assertEqual(escrow.amount, self.active_listing.price)
        self.assertEqual(escrow.title_snapshot, self.active_listing.title)
        self.assertEqual(escrow.description_snapshot, self.active_listing.description)
        self.assertEqual(escrow.status, EscrowTransaction.Status.PENDING)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.ESCROW_CREATED,
                object_type="escrow",
                object_id=escrow.id,
            ).exists()
        )

    def test_inactive_listing_blocked(self):
        self.authenticate(self.buyer)
        payload = {"listing_id": self.inactive_listing.id}

        response = self.client.post(reverse("create-escrow-from-listing"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(EscrowTransaction.objects.count(), 0)

    def test_self_purchase_blocked(self):
        self.authenticate(self.seller)
        payload = {"listing_id": self.active_listing.id}

        response = self.client.post(reverse("create-escrow-from-listing"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(EscrowTransaction.objects.count(), 0)

    def test_invalid_listing_blocked(self):
        self.authenticate(self.buyer)
        payload = {"listing_id": 999999}

        response = self.client.post(reverse("create-escrow-from-listing"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data["success"])
        self.assertEqual(EscrowTransaction.objects.count(), 0)

    def test_unauthenticated_request_blocked(self):
        payload = {"listing_id": self.active_listing.id}

        response = self.client.post(reverse("create-escrow-from-listing"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data["success"])
        self.assertEqual(EscrowTransaction.objects.count(), 0)


class EscrowReleaseTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="release_seller@example.com",
            password="StrongPass123!",
            first_name="Release",
            last_name="Seller",
        )
        self.buyer = User.objects.create_user(
            email="release_buyer@example.com",
            password="StrongPass123!",
            first_name="Release",
            last_name="Buyer",
        )
        self.other_user = User.objects.create_user(
            email="release_other@example.com",
            password="StrongPass123!",
            first_name="Release",
            last_name="Other",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Release Listing",
            description="Release flow listing",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("999.00"),
            is_active=True,
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
        self.disputed_escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.DISPUTED,
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

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_successful_release_action(self):
        self.authenticate(self.buyer)
        response = self.client.post(reverse("release-escrow", kwargs={"escrow_id": self.funded_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.funded_escrow.refresh_from_db()
        self.assertEqual(self.funded_escrow.status, EscrowTransaction.Status.RELEASED)

        self.assertEqual(PayoutRecord.objects.filter(escrow=self.funded_escrow).count(), 1)
        payout = PayoutRecord.objects.get(escrow=self.funded_escrow)
        self.assertEqual(payout.amount, self.funded_escrow.amount)
        self.assertEqual(payout.status, PayoutRecord.Status.PENDING)
        self.assertEqual(payout.initiated_by_id, self.buyer.id)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.RELEASE_TRIGGERED,
                object_type="escrow",
                object_id=self.funded_escrow.id,
            ).exists()
        )

    def test_unauthorized_release_blocked(self):
        self.authenticate(self.other_user)
        response = self.client.post(reverse("release-escrow", kwargs={"escrow_id": self.funded_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.funded_escrow.refresh_from_db()
        self.assertEqual(self.funded_escrow.status, EscrowTransaction.Status.FUNDED)
        self.assertEqual(PayoutRecord.objects.filter(escrow=self.funded_escrow).count(), 0)

    def test_double_release_blocked(self):
        self.authenticate(self.buyer)
        first = self.client.post(reverse("release-escrow", kwargs={"escrow_id": self.funded_escrow.id}), {}, format="json")
        second = self.client.post(reverse("release-escrow", kwargs={"escrow_id": self.funded_escrow.id}), {}, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(second.data["success"])
        self.assertEqual(PayoutRecord.objects.filter(escrow=self.funded_escrow).count(), 1)

    def test_disputed_escrow_cannot_be_released(self):
        self.authenticate(self.buyer)
        response = self.client.post(reverse("release-escrow", kwargs={"escrow_id": self.disputed_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.disputed_escrow.refresh_from_db()
        self.assertEqual(self.disputed_escrow.status, EscrowTransaction.Status.DISPUTED)
        self.assertEqual(PayoutRecord.objects.filter(escrow=self.disputed_escrow).count(), 0)

    def test_invalid_state_cannot_be_released(self):
        self.authenticate(self.buyer)
        response = self.client.post(reverse("release-escrow", kwargs={"escrow_id": self.pending_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.pending_escrow.refresh_from_db()
        self.assertEqual(self.pending_escrow.status, EscrowTransaction.Status.PENDING)
        self.assertEqual(PayoutRecord.objects.filter(escrow=self.pending_escrow).count(), 0)


class EscrowRefundTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="refund_seller@example.com",
            password="StrongPass123!",
            first_name="Refund",
            last_name="Seller",
        )
        self.buyer = User.objects.create_user(
            email="refund_buyer@example.com",
            password="StrongPass123!",
            first_name="Refund",
            last_name="Buyer",
        )
        self.other_user = User.objects.create_user(
            email="refund_other@example.com",
            password="StrongPass123!",
            first_name="Refund",
            last_name="Other",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Refund Listing",
            description="Refund flow listing",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("700.00"),
            is_active=True,
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
        self.released_escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.RELEASED,
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

        PaymentRecord.objects.create(
            escrow=self.funded_escrow,
            provider=PaymentRecord.Provider.PAYSTACK,
            reference="pay_ref_funded_001",
            amount=self.funded_escrow.amount,
            currency="NGN",
            status=PaymentRecord.Status.SUCCESS,
        )
        PaymentRecord.objects.create(
            escrow=self.released_escrow,
            provider=PaymentRecord.Provider.PAYSTACK,
            reference="pay_ref_released_001",
            amount=self.released_escrow.amount,
            currency="NGN",
            status=PaymentRecord.Status.SUCCESS,
        )
        PaymentRecord.objects.create(
            escrow=self.pending_escrow,
            provider=PaymentRecord.Provider.PAYSTACK,
            reference="pay_ref_pending_001",
            amount=self.pending_escrow.amount,
            currency="NGN",
            status=PaymentRecord.Status.SUCCESS,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    @patch("escrow.views.execute_paystack_refund_for_payment")
    def test_successful_refund_action(self, mock_execute_refund):
        mock_execute_refund.return_value = {
            "status": True,
            "data": {
                "id": 1111,
                "status": "success",
                "transaction_reference": "pay_ref_funded_001",
            },
        }

        self.authenticate(self.buyer)
        response = self.client.post(reverse("refund-escrow", kwargs={"escrow_id": self.funded_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.funded_escrow.refresh_from_db()
        self.assertEqual(self.funded_escrow.status, EscrowTransaction.Status.REFUNDED)
        self.assertEqual(RefundRecord.objects.filter(escrow=self.funded_escrow).count(), 1)
        refund = RefundRecord.objects.get(escrow=self.funded_escrow)
        self.assertEqual(refund.status, RefundRecord.Status.SUCCESS)
        self.assertEqual(refund.initiated_by_id, self.buyer.id)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.REFUND_TRIGGERED,
                object_type="escrow",
                object_id=self.funded_escrow.id,
            ).exists()
        )

    @patch("escrow.views.execute_paystack_refund_for_payment")
    def test_refund_blocked_after_release(self, mock_execute_refund):
        self.authenticate(self.buyer)
        response = self.client.post(reverse("refund-escrow", kwargs={"escrow_id": self.released_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(RefundRecord.objects.filter(escrow=self.released_escrow).count(), 0)
        mock_execute_refund.assert_not_called()

    @patch("escrow.views.execute_paystack_refund_for_payment")
    def test_duplicate_refund_blocked(self, mock_execute_refund):
        RefundRecord.objects.create(
            escrow=self.funded_escrow,
            reference="refund_existing_001",
            amount=self.funded_escrow.amount,
            currency="NGN",
            status=RefundRecord.Status.PROCESSING,
            initiated_by=self.buyer,
            metadata={"payment_reference": "pay_ref_funded_001"},
        )

        self.authenticate(self.buyer)
        response = self.client.post(reverse("refund-escrow", kwargs={"escrow_id": self.funded_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(RefundRecord.objects.filter(escrow=self.funded_escrow).count(), 1)
        mock_execute_refund.assert_not_called()

    @patch("escrow.views.execute_paystack_refund_for_payment")
    def test_unauthorized_refund_blocked(self, mock_execute_refund):
        self.authenticate(self.other_user)
        response = self.client.post(reverse("refund-escrow", kwargs={"escrow_id": self.funded_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.assertEqual(RefundRecord.objects.filter(escrow=self.funded_escrow).count(), 0)
        mock_execute_refund.assert_not_called()

    @patch("escrow.views.execute_paystack_refund_for_payment")
    def test_invalid_state_refund_blocked(self, mock_execute_refund):
        self.authenticate(self.buyer)
        response = self.client.post(reverse("refund-escrow", kwargs={"escrow_id": self.pending_escrow.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(RefundRecord.objects.filter(escrow=self.pending_escrow).count(), 0)
        mock_execute_refund.assert_not_called()
