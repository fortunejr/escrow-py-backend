from django.contrib import admin

from .models import Listing


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "seller", "listing_type", "price", "is_active", "created_at")
    list_filter = ("listing_type", "is_active", "created_at")
    search_fields = ("title", "description", "seller__email")
