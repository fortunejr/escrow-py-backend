from django.urls import path

from .views import (
    admin_escrow_detail_view,
    buyer_escrow_detail_view,
    create_escrow_from_listing_view,
    list_admin_escrows_view,
    list_my_escrows_view,
    list_seller_escrows_view,
    refund_escrow_view,
    release_escrow_view,
    seller_escrow_detail_view,
)

urlpatterns = [
    path("create/", create_escrow_from_listing_view, name="create-escrow-from-listing"),
    path("admin/", list_admin_escrows_view, name="list-admin-escrows"),
    path("admin/<int:escrow_id>/", admin_escrow_detail_view, name="admin-escrow-detail"),
    path("mine/", list_my_escrows_view, name="list-my-escrows"),
    path("mine/<int:escrow_id>/", buyer_escrow_detail_view, name="buyer-escrow-detail"),
    path("seller/", list_seller_escrows_view, name="list-seller-escrows"),
    path("seller/<int:escrow_id>/", seller_escrow_detail_view, name="seller-escrow-detail"),
    path("<int:escrow_id>/release/", release_escrow_view, name="release-escrow"),
    path("<int:escrow_id>/refund/", refund_escrow_view, name="refund-escrow"),
]
