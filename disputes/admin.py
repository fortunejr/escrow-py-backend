from django.contrib import admin

from .models import Dispute


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ("id", "escrow", "raised_by", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("reason", "raised_by__email", "escrow__id")
