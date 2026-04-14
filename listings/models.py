from django.conf import settings
from django.db import models


class Listing(models.Model):
    """Seller-owned marketplace offer used to initiate escrow transactions."""
    class ListingType(models.TextChoices):
        PRODUCT = "product", "Product"
        SERVICE = "service", "Service"

    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    listing_type = models.CharField(max_length=20, choices=ListingType.choices)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.title} ({self.seller.email})"
