from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken


User = get_user_model()


class ProfileManagementTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="profile_user@example.com",
            password="OldPass123!",
            first_name="Profile",
            last_name="User",
        )
        self.other_user = User.objects.create_user(
            email="other_profile_user@example.com",
            password="OtherPass123!",
            first_name="Other",
            last_name="User",
        )

    def authenticate(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_profile_update_success(self):
        self.authenticate(self.user)
        response = self.client.patch(
            reverse("profile"),
            {
                "first_name": "Updated",
                "last_name": "Name",
                "email": "updated_profile@example.com",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.last_name, "Name")
        self.assertEqual(self.user.email, "updated_profile@example.com")

    def test_profile_update_duplicate_email_rejected(self):
        self.authenticate(self.user)
        response = self.client.patch(
            reverse("profile"),
            {
                "first_name": "Updated",
                "last_name": "Name",
                "email": self.other_user.email,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("email", response.data["errors"])

    def test_profile_update_invalid_email_rejected(self):
        self.authenticate(self.user)
        response = self.client.patch(
            reverse("profile"),
            {
                "first_name": "Updated",
                "last_name": "Name",
                "email": "not-an-email",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("email", response.data["errors"])

    def test_profile_update_requires_authentication(self):
        response = self.client.patch(
            reverse("profile"),
            {
                "first_name": "Updated",
                "last_name": "Name",
                "email": "updated_profile@example.com",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data["success"])

    def test_change_password_success(self):
        self.authenticate(self.user)
        response = self.client.post(
            reverse("change-password"),
            {
                "current_password": "OldPass123!",
                "new_password": "NewStrongPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["success"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password("OldPass123!"))
        self.assertTrue(self.user.check_password("NewStrongPass123!"))

    def test_change_password_wrong_current_password_rejected(self):
        self.authenticate(self.user)
        response = self.client.post(
            reverse("change-password"),
            {
                "current_password": "WrongPass123!",
                "new_password": "NewStrongPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("current_password", response.data["errors"])

    def test_change_password_weak_password_rejected(self):
        self.authenticate(self.user)
        response = self.client.post(
            reverse("change-password"),
            {
                "current_password": "OldPass123!",
                "new_password": "12345",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(response.data["success"])
        self.assertIn("new_password", response.data["errors"])

    def test_change_password_requires_authentication(self):
        response = self.client.post(
            reverse("change-password"),
            {
                "current_password": "OldPass123!",
                "new_password": "NewStrongPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(response.data["success"])
