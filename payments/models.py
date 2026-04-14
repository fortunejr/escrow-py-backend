from django.conf import settings
from django.db import models
from django.db.models import Q


class SellerPayoutDetail(models.Model):
    """Seller payout destination details used for provider transfer execution."""
    class Provider(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="seller_payout_detail",
    )
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.PAYSTACK)
    bank_code = models.CharField(max_length=20)
    account_number = models.CharField(max_length=30)
    account_name = models.CharField(max_length=120, blank=True)
    currency = models.CharField(max_length=10, default="NGN")
    recipient_code = models.CharField(max_length=120, blank=True, null=True, db_index=True)
    recipient_reference = models.CharField(max_length=150, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["provider"]),
            models.Index(fields=["recipient_code"]),
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"PayoutDetail {self.user.email}"


class PaymentRecord(models.Model):
    """Provider payment attempt and verification data tied to one escrow."""
    class Provider(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"

    class Status(models.TextChoices):
        INITIALIZED = "initialized", "Initialized"
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    escrow = models.ForeignKey(
        "escrow.EscrowTransaction",
        on_delete=models.PROTECT,
        related_name="payment_records",
    )
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.PAYSTACK)
    reference = models.CharField(max_length=120, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIALIZED)
    authorization_url = models.URLField(blank=True, null=True)
    gateway_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["provider"]),
            models.Index(fields=["escrow"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="payment_amount_gt_zero"),
        ]

    def __str__(self):
        return f"Payment {self.reference} ({self.status})"


class PayoutRecord(models.Model):
    """Track payout intent/execution from released escrow to seller destination."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    escrow = models.ForeignKey(
        "escrow.EscrowTransaction",
        on_delete=models.PROTECT,
        related_name="payout_records",
    )
    reference = models.CharField(max_length=120, unique=True, db_index=True)
    provider_reference = models.CharField(max_length=150, blank=True, null=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_payouts",
    )
    processed_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["escrow"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="payout_amount_gt_zero"),
        ]

    def __str__(self):
        return f"Payout {self.reference} ({self.status})"


class RefundRecord(models.Model):
    """Track refund intent/execution tied to the original escrow funding payment."""
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REVERSED = "reversed", "Reversed"

    escrow = models.ForeignKey(
        "escrow.EscrowTransaction",
        on_delete=models.PROTECT,
        related_name="refund_records",
    )
    reference = models.CharField(max_length=120, unique=True, db_index=True)
    provider_reference = models.CharField(max_length=150, blank=True, null=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="NGN")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    reason = models.TextField(blank=True)
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initiated_refunds",
    )
    processed_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["escrow"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(amount__gt=0), name="refund_amount_gt_zero"),
        ]

    def __str__(self):
        return f"Refund {self.reference} ({self.status})"


class PaystackWebhookEvent(models.Model):
    """Persist received Paystack webhook events for idempotency and traceability."""
    event = models.CharField(max_length=80)
    event_id = models.CharField(max_length=120, blank=True, null=True, db_index=True)
    reference = models.CharField(max_length=120, blank=True, null=True, db_index=True)
    payload_hash = models.CharField(max_length=64, unique=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["event"]),
            models.Index(fields=["reference"]),
            models.Index(fields=["processed"]),
        ]

    def __str__(self):
        return f"Webhook {self.event} ({self.reference or 'no-ref'})"
