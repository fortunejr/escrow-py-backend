from django.contrib import admin

from .models import EscrowTransaction


@admin.register(EscrowTransaction)
class EscrowTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "listing",
        "buyer",
        "seller",
        "amount",
        "status",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "created_at", "updated_at")
    search_fields = (
        "title_snapshot",
        "description_snapshot",
        "listing__title",
        "buyer__email",
        "seller__email",
    )
