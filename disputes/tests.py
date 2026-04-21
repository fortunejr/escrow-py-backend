from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import AuditLog
from escrow.models import EscrowTransaction
from listings.models import Listing
from payments.models import PaymentRecord, PayoutRecord, RefundRecord

from .models import Dispute


User = get_user_model()


class DisputeFlowTests(APITestCase):
    def setUp(self):
        self.seller = User.objects.create_user(
            email="dispute_seller@example.com",
            password="StrongPass123!",
            first_name="Dispute",
            last_name="Seller",
        )
        self.buyer = User.objects.create_user(
            email="dispute_buyer@example.com",
            password="StrongPass123!",
            first_name="Dispute",
            last_name="Buyer",
        )
        self.other_user = User.objects.create_user(
            email="dispute_other@example.com",
            password="StrongPass123!",
            first_name="Dispute",
            last_name="Other",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Dispute Listing",
            description="Dispute lifecycle test",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("1200.00"),
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
        PaymentRecord.objects.create(
            escrow=self.funded_escrow,
            provider=PaymentRecord.Provider.PAYSTACK,
            reference="dispute_pay_ref_001",
            amount=self.funded_escrow.amount,
            currency="NGN",
            status=PaymentRecord.Status.SUCCESS,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def raise_dispute(self, user, escrow_id, reason="Issue with delivery"):
        self.authenticate(user)
        return self.client.post(
            reverse("create-dispute"),
            {"escrow_id": escrow_id, "reason": reason},
            format="json",
        )

    def test_buyer_can_raise_dispute(self):
        response = self.raise_dispute(self.buyer, self.funded_escrow.id)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(Dispute.objects.count(), 1)

        dispute = Dispute.objects.first()
        self.assertEqual(dispute.raised_by_id, self.buyer.id)
        self.funded_escrow.refresh_from_db()
        self.assertEqual(self.funded_escrow.status, EscrowTransaction.Status.DISPUTED)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.DISPUTE_OPENED,
                object_type="dispute",
                object_id=dispute.id,
            ).exists()
        )

    def test_seller_can_raise_dispute(self):
        second_escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.FUNDED,
        )

        response = self.raise_dispute(self.seller, second_escrow.id, reason="Buyer requested unexpected changes.")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        dispute = Dispute.objects.get(escrow=second_escrow)
        self.assertEqual(dispute.raised_by_id, self.seller.id)
        second_escrow.refresh_from_db()
        self.assertEqual(second_escrow.status, EscrowTransaction.Status.DISPUTED)

    def test_unrelated_user_blocked(self):
        response = self.raise_dispute(self.other_user, self.funded_escrow.id)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.assertEqual(Dispute.objects.count(), 0)

    def test_release_blocked_during_dispute(self):
        self.raise_dispute(self.buyer, self.funded_escrow.id)

        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("release-escrow", kwargs={"escrow_id": self.funded_escrow.id}),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.funded_escrow.refresh_from_db()
        self.assertEqual(self.funded_escrow.status, EscrowTransaction.Status.DISPUTED)

    def test_refund_blocked_during_dispute(self):
        self.raise_dispute(self.buyer, self.funded_escrow.id)

        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("refund-escrow", kwargs={"escrow_id": self.funded_escrow.id}),
            {"reason": "Need refund"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertEqual(RefundRecord.objects.filter(escrow=self.funded_escrow).count(), 0)


class AdminDisputeResolutionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_dispute@example.com",
            password="StrongPass123!",
            first_name="Admin",
            last_name="User",
            is_staff=True,
        )
        self.seller = User.objects.create_user(
            email="seller_admin_dispute@example.com",
            password="StrongPass123!",
            first_name="Admin",
            last_name="Seller",
        )
        self.buyer = User.objects.create_user(
            email="buyer_admin_dispute@example.com",
            password="StrongPass123!",
            first_name="Admin",
            last_name="Buyer",
        )
        self.listing = Listing.objects.create(
            seller=self.seller,
            title="Admin Resolution Listing",
            description="Admin dispute resolution",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("1000.00"),
            is_active=True,
        )
        self.escrow = EscrowTransaction.objects.create(
            listing=self.listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=self.listing.price,
            title_snapshot=self.listing.title,
            description_snapshot=self.listing.description,
            status=EscrowTransaction.Status.DISPUTED,
        )
        PaymentRecord.objects.create(
            escrow=self.escrow,
            provider=PaymentRecord.Provider.PAYSTACK,
            reference="admin_dispute_pay_ref_001",
            amount=self.escrow.amount,
            currency="NGN",
            status=PaymentRecord.Status.SUCCESS,
        )
        self.dispute = Dispute.objects.create(
            escrow=self.escrow,
            raised_by=self.buyer,
            reason="Need admin intervention",
            status=Dispute.Status.OPEN,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_admin_resolves_with_release(self):
        self.authenticate(self.admin)
        response = self.client.post(
            reverse("resolve-dispute", kwargs={"dispute_id": self.dispute.id}),
            {"outcome": "release", "resolution_notes": "Item delivered, release to seller."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.dispute.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.dispute.status, Dispute.Status.RESOLVED)
        self.assertEqual(self.dispute.resolution_outcome, Dispute.ResolutionOutcome.RELEASE)
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.RELEASED)
        self.assertEqual(PayoutRecord.objects.filter(escrow=self.escrow).count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.DISPUTE_RESOLVED,
                object_type="dispute",
                object_id=self.dispute.id,
            ).exists()
        )

    @patch("disputes.views.execute_paystack_refund_for_payment")
    def test_admin_resolves_with_refund(self, mock_execute_refund):
        mock_execute_refund.return_value = {
            "status": True,
            "data": {
                "id": 202020,
                "status": "success",
                "transaction_reference": "admin_dispute_pay_ref_001",
            },
        }

        self.authenticate(self.admin)
        response = self.client.post(
            reverse("resolve-dispute", kwargs={"dispute_id": self.dispute.id}),
            {"outcome": "refund", "resolution_notes": "Refund approved."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])

        self.dispute.refresh_from_db()
        self.escrow.refresh_from_db()
        self.assertEqual(self.dispute.status, Dispute.Status.RESOLVED)
        self.assertEqual(self.dispute.resolution_outcome, Dispute.ResolutionOutcome.REFUND)
        self.assertEqual(self.escrow.status, EscrowTransaction.Status.REFUNDED)
        self.assertEqual(RefundRecord.objects.filter(escrow=self.escrow).count(), 1)
        refund = RefundRecord.objects.get(escrow=self.escrow)
        self.assertEqual(refund.status, RefundRecord.Status.SUCCESS)
        self.assertTrue(
            AuditLog.objects.filter(
                action=AuditLog.Action.DISPUTE_RESOLVED,
                object_type="dispute",
                object_id=self.dispute.id,
            ).exists()
        )

    def test_non_admin_blocked(self):
        self.authenticate(self.buyer)
        response = self.client.post(
            reverse("resolve-dispute", kwargs={"dispute_id": self.dispute.id}),
            {"outcome": "release"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.dispute.refresh_from_db()
        self.assertEqual(self.dispute.status, Dispute.Status.OPEN)

    def test_already_resolved_dispute_cannot_be_resolved_again(self):
        self.dispute.status = Dispute.Status.RESOLVED
        self.dispute.resolution_outcome = Dispute.ResolutionOutcome.RELEASE
        self.dispute.save(update_fields=["status", "resolution_outcome", "updated_at"])

        self.authenticate(self.admin)
        response = self.client.post(
            reverse("resolve-dispute", kwargs={"dispute_id": self.dispute.id}),
            {"outcome": "refund"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])


class AdminDisputeListTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_dispute_list@example.com",
            password="StrongPass123!",
            first_name="Admin",
            last_name="DisputeList",
            is_staff=True,
        )
        self.non_admin = User.objects.create_user(
            email="non_admin_dispute_list@example.com",
            password="StrongPass123!",
            first_name="Non",
            last_name="Admin",
        )
        self.seller = User.objects.create_user(
            email="admin_dispute_list_seller@example.com",
            password="StrongPass123!",
            first_name="Seller",
            last_name="User",
        )
        self.buyer = User.objects.create_user(
            email="admin_dispute_list_buyer@example.com",
            password="StrongPass123!",
            first_name="Buyer",
            last_name="User",
        )
        listing = Listing.objects.create(
            seller=self.seller,
            title="Admin Dispute List Listing",
            description="Admin dispute list test listing",
            listing_type=Listing.ListingType.PRODUCT,
            price=Decimal("750.00"),
            is_active=True,
        )
        escrow = EscrowTransaction.objects.create(
            listing=listing,
            buyer=self.buyer,
            seller=self.seller,
            amount=listing.price,
            title_snapshot=listing.title,
            description_snapshot=listing.description,
            status=EscrowTransaction.Status.DISPUTED,
        )
        Dispute.objects.create(
            escrow=escrow,
            raised_by=self.buyer,
            reason="Admin list endpoint test",
            status=Dispute.Status.OPEN,
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_admin_can_list_all_disputes(self):
        self.authenticate(self.admin)
        response = self.client.get(reverse("list-admin-disputes"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertEqual(len(response.data["data"]), 1)

    def test_non_admin_cannot_list_all_disputes(self):
        self.authenticate(self.non_admin)
        response = self.client.get(reverse("list-admin-disputes"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        self.assertIn("permission", response.data["errors"])
