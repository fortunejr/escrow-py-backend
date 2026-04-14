from django.urls import path

from .views import (
    create_listing_view,
    deactivate_listing_view,
    list_active_listings_view,
    listing_detail_view,
    update_listing_view,
)

urlpatterns = [
    path("", list_active_listings_view, name="list-active-listings"),
    path("create/", create_listing_view, name="create-listing"),
    path("<int:listing_id>/", listing_detail_view, name="listing-detail"),
    path("<int:listing_id>/update/", update_listing_view, name="update-listing"),
    path("<int:listing_id>/deactivate/", deactivate_listing_view, name="deactivate-listing"),
]
