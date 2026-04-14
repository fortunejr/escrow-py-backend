from django.conf import settings
from django.db import models
from django.db.models import F, Q


class EscrowTransaction(models.Model):
    """Snapshot of buyer-seller agreement and state across the escrow lifecycle."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAYMENT_PENDING = "payment_pending", "Payment Pending"
        FUNDED = "funded", "Funded"
        RELEASED = "released", "Released"
        REFUNDED = "refunded", "Refunded"
        DISPUTED = "disputed", "Disputed"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    listing = models.ForeignKey(
        "listings.Listing",
        on_delete=models.PROTECT,
        related_name="escrow_transactions",
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="buyer_escrows",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="seller_escrows",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    title_snapshot = models.CharField(max_length=200)
    description_snapshot = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["buyer"]),
            models.Index(fields=["seller"]),
            models.Index(fields=["listing"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="escrow_amount_gt_zero"),
            models.CheckConstraint(check=~Q(buyer=F("seller")), name="escrow_buyer_not_seller"),
        ]

    def __str__(self):
        return f"Escrow #{self.id} - {self.status}"
