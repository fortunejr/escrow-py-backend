from django.urls import path

from .views import (
    buyer_escrow_detail_view,
    create_escrow_from_listing_view,
    list_my_escrows_view,
    refund_escrow_view,
    release_escrow_view,
)

urlpatterns = [
    path("create/", create_escrow_from_listing_view, name="create-escrow-from-listing"),
    path("mine/", list_my_escrows_view, name="list-my-escrows"),
    path("mine/<int:escrow_id>/", buyer_escrow_detail_view, name="buyer-escrow-detail"),
    path("<int:escrow_id>/release/", release_escrow_view, name="release-escrow"),
    path("<int:escrow_id>/refund/", refund_escrow_view, name="refund-escrow"),
]
