from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.audit import log_audit_event
from core.models import AuditLog

from .models import Listing
from .serializers import ListingSerializer


def build_response(success, message, data=None, errors=None):
    return {
        "success": success,
        "message": message,
        "data": data,
        "errors": errors,
    }


def validate_listing_type(value):
    valid_types = {choice[0] for choice in Listing.ListingType.choices}
    if value not in valid_types:
        return False
    return True


def parse_price(value):
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if price <= 0:
        return None
    return price


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_listing_view(request):
    """Create a listing owned by the authenticated seller."""
    title = request.data.get("title")
    description = request.data.get("description")
    listing_type = request.data.get("listing_type")
    price_input = request.data.get("price")

    errors = {}

    if not title or not isinstance(title, str):
        errors["title"] = ["Title is required."]
    if not description or not isinstance(description, str):
        errors["description"] = ["Description is required."]
    if not listing_type or not isinstance(listing_type, str):
        errors["listing_type"] = ["Listing type is required."]
    elif not validate_listing_type(listing_type):
        errors["listing_type"] = ["Listing type must be 'product' or 'service'."]

    price = parse_price(price_input)
    if price is None:
        errors["price"] = ["Price must be a valid number greater than 0."]

    if errors:
        return Response(
            build_response(False, "Listing creation failed.", data=None, errors=errors),
            status=status.HTTP_400_BAD_REQUEST,
        )

    listing = Listing.objects.create(
        seller=request.user,
        title=title.strip(),
        description=description.strip(),
        listing_type=listing_type,
        price=price,
    )
    log_audit_event(
        actor=request.user,
        action=AuditLog.Action.LISTING_CREATED,
        object_type="listing",
        object_id=listing.id,
        metadata={
            "seller_id": listing.seller_id,
            "listing_type": listing.listing_type,
            "price": str(listing.price),
            "is_active": listing.is_active,
        },
    )

    return Response(
        build_response(True, "Listing created successfully.", data=ListingSerializer(listing).data, errors=None),
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def list_active_listings_view(request):
    """Public endpoint for active listings only."""
    listings = Listing.objects.filter(is_active=True).select_related("seller")
    data = ListingSerializer(listings, many=True).data
    return Response(
        build_response(True, "Active listings fetched successfully.", data=data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([AllowAny])
def listing_detail_view(request, listing_id):
    """Retrieve one listing, hiding inactive listings from non-owners."""
    listing = Listing.objects.filter(id=listing_id).select_related("seller").first()

    if not listing:
        return Response(
            build_response(False, "Listing not found.", data=None, errors={"listing": ["Listing does not exist."]}),
            status=status.HTTP_404_NOT_FOUND,
        )

    if not listing.is_active:
        is_owner = request.user.is_authenticated and listing.seller_id == request.user.id
        if not is_owner:
            return Response(
                build_response(False, "Listing not found.", data=None, errors={"listing": ["Listing does not exist."]}),
                status=status.HTTP_404_NOT_FOUND,
            )

    return Response(
        build_response(True, "Listing detail fetched successfully.", data=ListingSerializer(listing).data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_listing_view(request, listing_id):
    """Allow a seller to update only their own listing."""
    listing = Listing.objects.filter(id=listing_id).first()

    if not listing:
        return Response(
            build_response(False, "Listing not found.", data=None, errors={"listing": ["Listing does not exist."]}),
            status=status.HTTP_404_NOT_FOUND,
        )

    if listing.seller_id != request.user.id:
        return Response(
            build_response(False, "Permission denied.", data=None, errors={"permission": ["You do not own this listing."]}),
            status=status.HTTP_403_FORBIDDEN,
        )

    title = request.data.get("title", None)
    description = request.data.get("description", None)
    listing_type = request.data.get("listing_type", None)
    price_input = request.data.get("price", None)
    is_active = request.data.get("is_active", None)

    errors = {}
    has_update = False

    if title is not None:
        if not isinstance(title, str) or not title.strip():
            errors["title"] = ["Title must be a non-empty string."]
        else:
            listing.title = title.strip()
            has_update = True

    if description is not None:
        if not isinstance(description, str) or not description.strip():
            errors["description"] = ["Description must be a non-empty string."]
        else:
            listing.description = description.strip()
            has_update = True

    if listing_type is not None:
        if not isinstance(listing_type, str) or not validate_listing_type(listing_type):
            errors["listing_type"] = ["Listing type must be 'product' or 'service'."]
        else:
            listing.listing_type = listing_type
            has_update = True

    if price_input is not None:
        price = parse_price(price_input)
        if price is None:
            errors["price"] = ["Price must be a valid number greater than 0."]
        else:
            listing.price = price
            has_update = True

    if is_active is not None:
        if not isinstance(is_active, bool):
            errors["is_active"] = ["is_active must be a boolean."]
        else:
            listing.is_active = is_active
            has_update = True

    if errors:
        return Response(
            build_response(False, "Listing update failed.", data=None, errors=errors),
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not has_update:
        return Response(
            build_response(
                False,
                "Listing update failed.",
                data=None,
                errors={"fields": ["Provide at least one valid field to update."]},
            ),
            status=status.HTTP_400_BAD_REQUEST,
        )

    listing.save()
    listing.refresh_from_db()
    return Response(
        build_response(True, "Listing updated successfully.", data=ListingSerializer(listing).data, errors=None),
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def deactivate_listing_view(request, listing_id):
    """Soft-deactivate a listing owned by the authenticated seller."""
    listing = Listing.objects.filter(id=listing_id).first()

    if not listing:
        return Response(
            build_response(False, "Listing not found.", data=None, errors={"listing": ["Listing does not exist."]}),
            status=status.HTTP_404_NOT_FOUND,
        )

    if listing.seller_id != request.user.id:
        return Response(
            build_response(False, "Permission denied.", data=None, errors={"permission": ["You do not own this listing."]}),
            status=status.HTTP_403_FORBIDDEN,
        )

    if not listing.is_active:
        return Response(
            build_response(True, "Listing is already inactive.", data=ListingSerializer(listing).data, errors=None),
            status=status.HTTP_200_OK,
        )

    listing.is_active = False
    listing.save(update_fields=["is_active", "updated_at"])
    listing.refresh_from_db()
    return Response(
        build_response(True, "Listing deactivated successfully.", data=ListingSerializer(listing).data, errors=None),
        status=status.HTTP_200_OK,
    )
