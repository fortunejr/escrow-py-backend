from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Immutable audit trail for critical financial and lifecycle events."""
    class Action(models.TextChoices):
        LISTING_CREATED = "listing_created", "Listing Created"
        ESCROW_CREATED = "escrow_created", "Escrow Created"
        PAYMENT_INITIALIZED = "payment_initialized", "Payment Initialized"
        PAYMENT_VERIFIED = "payment_verified", "Payment Verified"
        ESCROW_FUNDED = "escrow_funded", "Escrow Funded"
        RELEASE_TRIGGERED = "release_triggered", "Release Triggered"
        PAYOUT_EXECUTED = "payout_executed", "Payout Executed"
        REFUND_TRIGGERED = "refund_triggered", "Refund Triggered"
        DISPUTE_OPENED = "dispute_opened", "Dispute Opened"
        DISPUTE_RESOLVED = "dispute_resolved", "Dispute Resolved"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=64, choices=Action.choices, db_index=True)
    object_type = models.CharField(max_length=64, db_index=True)
    object_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    object_ref = models.CharField(max_length=255, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["action"]),
            models.Index(fields=["object_type", "object_id"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.action} [{self.object_ref}]"
