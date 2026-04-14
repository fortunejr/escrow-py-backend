from django.urls import path

from .views import create_escrow_from_listing_view, refund_escrow_view, release_escrow_view

urlpatterns = [
    path("create/", create_escrow_from_listing_view, name="create-escrow-from-listing"),
    path("<int:escrow_id>/release/", release_escrow_view, name="release-escrow"),
    path("<int:escrow_id>/refund/", refund_escrow_view, name="refund-escrow"),
]
