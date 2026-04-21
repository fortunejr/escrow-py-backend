from django.urls import path

from .views import (
    initialize_escrow_payment_view,
    list_my_payouts_view,
    my_payout_detail_view,
    paystack_webhook_view,
    execute_payout_view,
    upsert_seller_payout_detail_view,
    update_seller_payout_detail_view,
    verify_escrow_payment_view,
)

urlpatterns = [
    path("initialize/", initialize_escrow_payment_view, name="initialize-escrow-payment"),
    path("verify/", verify_escrow_payment_view, name="verify-escrow-payment"),
    path("webhooks/paystack/", paystack_webhook_view, name="paystack-webhook"),
    path("payout-details/", upsert_seller_payout_detail_view, name="upsert-seller-payout-detail"),
    path("payout-details/update/", update_seller_payout_detail_view, name="update-seller-payout-detail"),
    path("payouts/mine/", list_my_payouts_view, name="list-my-payouts"),
    path("payouts/mine/<int:payout_id>/", my_payout_detail_view, name="my-payout-detail"),
    path("payouts/<int:payout_id>/execute/", execute_payout_view, name="execute-payout"),
]
