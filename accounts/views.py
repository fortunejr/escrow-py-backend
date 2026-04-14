from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .models import User


def build_response(success, message, data=None, errors=None):
    return {
        "success": success,
        "message": message,
        "data": data,
        "errors": errors,
    }


def serialize_user(user):
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_active": user.is_active,
        "date_joined": user.date_joined,
    }


@api_view(["POST"])
@permission_classes([AllowAny])
def register_view(request):
    email = request.data.get("email")
    password = request.data.get("password")
    first_name = request.data.get("first_name", "")
    last_name = request.data.get("last_name", "")

    errors = {}

    if not email or not isinstance(email, str):
        errors["email"] = ["Email is required."]
    else:
        email = email.strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            errors["email"] = ["Enter a valid email address."]

    if not password or not isinstance(password, str):
        errors["password"] = ["Password is required."]

    if email and User.objects.filter(email__iexact=email).exists():
        errors["email"] = ["An account with this email already exists."]

    if password and isinstance(password, str):
        temp_user = User(email=email or "")
        try:
            validate_password(password, user=temp_user)
        except ValidationError as exc:
            errors["password"] = list(exc.messages)

    if errors:
        return Response(
            build_response(False, "Registration failed.", data=None, errors=errors),
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = User.objects.create_user(
        email=email,
        password=password,
        first_name=str(first_name).strip(),
        last_name=str(last_name).strip(),
    )

    refresh = RefreshToken.for_user(user)
    data = {
        "user": serialize_user(user),
        "tokens": {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        },
    }
    return Response(
        build_response(True, "Registration successful.", data=data, errors=None),
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    email = request.data.get("email")
    password = request.data.get("password")

    errors = {}

    if not email or not isinstance(email, str):
        errors["email"] = ["Email is required."]
    if not password or not isinstance(password, str):
        errors["password"] = ["Password is required."]

    if errors:
        return Response(
            build_response(False, "Login failed.", data=None, errors=errors),
            status=status.HTTP_400_BAD_REQUEST,
        )

    email = email.strip().lower()
    user = authenticate(request, email=email, password=password)

    if not user:
        return Response(
            build_response(
                False,
                "Invalid credentials.",
                data=None,
                errors={"non_field_errors": ["Invalid email or password."]},
            ),
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        return Response(
            build_response(
                False,
                "Account is inactive.",
                data=None,
                errors={"account": ["This account is inactive."]},
            ),
            status=status.HTTP_403_FORBIDDEN,
        )

    refresh = RefreshToken.for_user(user)
    data = {
        "user": serialize_user(user),
        "tokens": {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        },
    }
    return Response(
        build_response(True, "Login successful.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([AllowAny])
def token_refresh_view(request):
    refresh_token = request.data.get("refresh")

    if not refresh_token:
        return Response(
            build_response(
                False,
                "Token refresh failed.",
                data=None,
                errors={"refresh": ["Refresh token is required."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        token = RefreshToken(refresh_token)
    except TokenError:
        return Response(
            build_response(
                False,
                "Token refresh failed.",
                data=None,
                errors={"refresh": ["Refresh token is invalid or expired."]},
            ),
            status=status.HTTP_401_UNAUTHORIZED,
        )

    data = {"access": str(token.access_token)}
    return Response(
        build_response(True, "Token refreshed successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def profile_view(request):
    return Response(
        build_response(True, "Profile fetched successfully.", data=serialize_user(request.user), errors=None),
        status=status.HTTP_200_OK,
    )
