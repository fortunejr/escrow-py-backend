from django.conf import settings
from django.db import models


class Dispute(models.Model):
    """Formal challenge raised by escrow buyer/seller, resolved by admin flow."""
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        REJECTED = "rejected", "Rejected"

    class ResolutionOutcome(models.TextChoices):
        RELEASE = "release", "Release Funds"
        REFUND = "refund", "Refund Buyer"

    escrow = models.ForeignKey(
        "escrow.EscrowTransaction",
        on_delete=models.PROTECT,
        related_name="disputes",
    )
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="raised_disputes",
    )
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    resolution_outcome = models.CharField(
        max_length=20,
        choices=ResolutionOutcome.choices,
        blank=True,
        null=True,
    )
    resolution_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["escrow"]),
            models.Index(fields=["raised_by"]),
        ]

    def __str__(self):
        return f"Dispute #{self.id} ({self.status})"
