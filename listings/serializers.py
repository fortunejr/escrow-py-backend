from rest_framework import serializers

from .models import Listing


class ListingSerializer(serializers.ModelSerializer):
    seller = serializers.SerializerMethodField()
    listing_type_display = serializers.CharField(source="get_listing_type_display", read_only=True)

    class Meta:
        model = Listing
        fields = (
            "id",
            "seller",
            "title",
            "description",
            "listing_type",
            "listing_type_display",
            "price",
            "is_active",
            "created_at",
            "updated_at",
        )

    def get_seller(self, obj):
        full_name = f"{obj.seller.first_name} {obj.seller.last_name}".strip()
        return {
            "id": obj.seller.id,
            "email": obj.seller.email,
            "name": full_name or obj.seller.email,
        }
