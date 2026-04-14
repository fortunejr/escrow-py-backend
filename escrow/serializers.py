from rest_framework import serializers

from .models import EscrowTransaction


class EscrowTransactionSerializer(serializers.ModelSerializer):
    buyer = serializers.SerializerMethodField()
    seller = serializers.SerializerMethodField()
    listing = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = EscrowTransaction
        fields = (
            "id",
            "listing",
            "buyer",
            "seller",
            "amount",
            "title_snapshot",
            "description_snapshot",
            "status",
            "status_display",
            "created_at",
            "updated_at",
        )

    def get_buyer(self, obj):
        full_name = f"{obj.buyer.first_name} {obj.buyer.last_name}".strip()
        return {
            "id": obj.buyer.id,
            "email": obj.buyer.email,
            "name": full_name or obj.buyer.email,
        }

    def get_seller(self, obj):
        full_name = f"{obj.seller.first_name} {obj.seller.last_name}".strip()
        return {
            "id": obj.seller.id,
            "email": obj.seller.email,
            "name": full_name or obj.seller.email,
        }

    def get_listing(self, obj):
        return {
            "id": obj.listing.id,
            "title": obj.listing.title,
            "listing_type": obj.listing.listing_type,
            "is_active": obj.listing.is_active,
        }
