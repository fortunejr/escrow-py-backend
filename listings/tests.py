import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import AuditLog

from .models import Listing


User = get_user_model()


class ListingAPITests(APITestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()

        self.seller = User.objects.create_user(
            email="seller@example.com",
            password="StrongPass123!",
            first_name="Seller",
            last_name="One",
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="StrongPass123!",
            first_name="Other",
            last_name="User",
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_create_listing_success(self):
        self.authenticate(self.seller)
        payload = {
            "title": "MacBook Pro",
            "description": "16 inch model",
            "listing_type": "product",
            "price": "2500.00",
        }
        response = self.client.post(reverse("create-listing"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["data"]["title"], "MacBook Pro")
        self.assertEqual(Listing.objects.count(), 1)
        self.assertEqual(Listing.objects.first().seller, self.seller)
        self.assertEqual(
            AuditLog.objects.filter(action=AuditLog.Action.LISTING_CREATED, object_type="listing").count(),
            1,
        )

    def test_create_listing_with_image_upload(self):
        self.authenticate(self.seller)
        image = SimpleUploadedFile("listing.jpg", b"fake-image-content", content_type="image/jpeg")
        payload = {
            "title": "Camera",
            "description": "Mirrorless camera",
            "listing_type": "product",
            "price": "600.00",
            "image": image,
        }

        response = self.client.post(reverse("create-listing"), payload, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["success"])
        self.assertIn("/media/listings/", response.data["data"]["image"])
        self.assertTrue(Listing.objects.first().image.name.startswith("listings/"))

    def test_create_listing_requires_authentication(self):
        payload = {
            "title": "Website Development",
            "description": "Business website package",
            "listing_type": "service",
            "price": "800.00",
        }
        response = self.client.post(reverse("create-listing"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data["success"])

    def test_public_list_and_detail(self):
        image = SimpleUploadedFile("active.jpg", b"active-image", content_type="image/jpeg")
        active_listing = Listing.objects.create(
            seller=self.seller,
            title="Active Listing",
            description="Visible listing",
            listing_type="product",
            price=Decimal("100.00"),
            is_active=True,
            image=image,
        )
        Listing.objects.create(
            seller=self.seller,
            title="Inactive Listing",
            description="Hidden listing",
            listing_type="service",
            price=Decimal("50.00"),
            is_active=False,
        )

        list_response = self.client.get(reverse("list-active-listings"))
        detail_response = self.client.get(reverse("listing-detail", kwargs={"listing_id": active_listing.id}))

        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertTrue(list_response.data["success"])
        self.assertEqual(len(list_response.data["data"]), 1)
        self.assertEqual(list_response.data["data"][0]["id"], active_listing.id)
        self.assertIn("/media/listings/", list_response.data["data"][0]["image"])

        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        self.assertTrue(detail_response.data["success"])
        self.assertEqual(detail_response.data["data"]["id"], active_listing.id)

    def test_owner_can_update_own_listing(self):
        listing = Listing.objects.create(
            seller=self.seller,
            title="Old Title",
            description="Old Description",
            listing_type="product",
            price=Decimal("100.00"),
        )
        self.authenticate(self.seller)
        payload = {"title": "New Title", "price": "120.00"}

        response = self.client.patch(reverse("update-listing", kwargs={"listing_id": listing.id}), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        listing.refresh_from_db()
        self.assertEqual(listing.title, "New Title")
        self.assertEqual(listing.price, Decimal("120.00"))

    def test_owner_can_update_listing_image(self):
        listing = Listing.objects.create(
            seller=self.seller,
            title="Image Listing",
            description="Needs image",
            listing_type="product",
            price=Decimal("100.00"),
        )
        self.authenticate(self.seller)
        image = SimpleUploadedFile("updated.jpg", b"updated-image-content", content_type="image/jpeg")

        response = self.client.patch(
            reverse("update-listing", kwargs={"listing_id": listing.id}),
            {"image": image},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.assertIn("/media/listings/", response.data["data"]["image"])
        listing.refresh_from_db()
        self.assertTrue(listing.image.name.startswith("listings/"))

    def test_non_owner_cannot_update_listing(self):
        listing = Listing.objects.create(
            seller=self.seller,
            title="Protected Listing",
            description="Cannot edit",
            listing_type="service",
            price=Decimal("300.00"),
        )
        self.authenticate(self.other_user)
        response = self.client.patch(
            reverse("update-listing", kwargs={"listing_id": listing.id}),
            {"title": "Hacked"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        listing.refresh_from_db()
        self.assertEqual(listing.title, "Protected Listing")

    def test_owner_can_deactivate_listing(self):
        listing = Listing.objects.create(
            seller=self.seller,
            title="To Deactivate",
            description="Will be inactive",
            listing_type="product",
            price=Decimal("99.99"),
        )
        self.authenticate(self.seller)
        response = self.client.post(reverse("deactivate-listing", kwargs={"listing_id": listing.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        listing.refresh_from_db()
        self.assertFalse(listing.is_active)

    def test_non_owner_cannot_deactivate_listing(self):
        listing = Listing.objects.create(
            seller=self.seller,
            title="Seller Listing",
            description="Only owner can deactivate",
            listing_type="product",
            price=Decimal("44.00"),
        )
        self.authenticate(self.other_user)
        response = self.client.post(reverse("deactivate-listing", kwargs={"listing_id": listing.id}), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(response.data["success"])
        listing.refresh_from_db()
        self.assertTrue(listing.is_active)
