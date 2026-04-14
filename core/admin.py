from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "object_ref", "actor", "created_at")
    list_filter = ("action", "object_type", "created_at")
    search_fields = ("object_ref", "actor__email")
    readonly_fields = ("created_at",)
