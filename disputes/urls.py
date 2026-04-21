from django.urls import path

from .views import (
    create_dispute_view,
    dispute_detail_view,
    list_admin_disputes_view,
    list_disputes_view,
    resolve_dispute_view,
)

urlpatterns = [
    path("", list_disputes_view, name="list-disputes"),
    path("admin/", list_admin_disputes_view, name="list-admin-disputes"),
    path("create/", create_dispute_view, name="create-dispute"),
    path("<int:dispute_id>/resolve/", resolve_dispute_view, name="resolve-dispute"),
    path("<int:dispute_id>/", dispute_detail_view, name="dispute-detail"),
]
