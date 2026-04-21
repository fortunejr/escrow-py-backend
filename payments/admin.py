from django.contrib import admin

from .models import (
    PaymentRecord,
    PaystackWebhookEvent,
    PayoutRecord,
    RefundRecord,
    SellerPayoutDetail,
)


@admin.register(SellerPayoutDetail)
class SellerPayoutDetailAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "provider",
        "bank_code",
        "account_number",
        "account_name",
        "currency",
        "recipient_code",
        "is_active",
        "updated_at",
    )
    list_filter = ("provider", "currency", "is_active", "created_at", "updated_at")
    search_fields = ("user__email", "account_number", "account_name", "bank_code", "recipient_code")


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reference",
        "escrow",
        "provider",
        "amount",
        "currency",
        "status",
        "created_at",
        "updated_at",
    )
    list_filter = ("provider", "status", "currency", "created_at", "updated_at")
    search_fields = ("reference", "escrow__id", "escrow__buyer__email", "escrow__seller__email")


@admin.register(PayoutRecord)
class PayoutRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reference",
        "provider_reference",
        "escrow",
        "amount",
        "currency",
        "status",
        "initiated_by",
        "processed_at",
        "created_at",
    )
    list_filter = ("status", "currency", "created_at", "processed_at")
    search_fields = (
        "reference",
        "provider_reference",
        "escrow__id",
        "escrow__seller__email",
        "initiated_by__email",
    )


@admin.register(RefundRecord)
class RefundRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reference",
        "provider_reference",
        "escrow",
        "amount",
        "currency",
        "status",
        "initiated_by",
        "processed_at",
        "created_at",
    )
    list_filter = ("status", "currency", "created_at", "processed_at")
    search_fields = (
        "reference",
        "provider_reference",
        "reason",
        "escrow__id",
        "escrow__buyer__email",
        "initiated_by__email",
    )


@admin.register(PaystackWebhookEvent)
class PaystackWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "event_id", "reference", "processed", "created_at", "processed_at")
    list_filter = ("event", "processed", "created_at", "processed_at")
    search_fields = ("event", "event_id", "reference", "payload_hash")
