from rest_framework import serializers

from .models import Dispute


class DisputeSerializer(serializers.ModelSerializer):
    escrow = serializers.SerializerMethodField()
    raised_by = serializers.SerializerMethodField()
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    resolution_outcome_display = serializers.SerializerMethodField()

    class Meta:
        model = Dispute
        fields = (
            "id",
            "escrow",
            "raised_by",
            "reason",
            "status",
            "status_display",
            "resolution_outcome",
            "resolution_outcome_display",
            "resolution_notes",
            "created_at",
            "updated_at",
        )

    def get_escrow(self, obj):
        return {
            "id": obj.escrow.id,
            "status": obj.escrow.status,
            "buyer_id": obj.escrow.buyer_id,
            "seller_id": obj.escrow.seller_id,
            "amount": str(obj.escrow.amount),
        }

    def get_raised_by(self, obj):
        full_name = f"{obj.raised_by.first_name} {obj.raised_by.last_name}".strip()
        return {
            "id": obj.raised_by.id,
            "email": obj.raised_by.email,
            "name": full_name or obj.raised_by.email,
        }

    def get_resolution_outcome_display(self, obj):
        return obj.get_resolution_outcome_display() if obj.resolution_outcome else None
