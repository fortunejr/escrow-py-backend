from django.urls import path

from .views import change_password_view, login_view, profile_view, register_view, token_refresh_view

urlpatterns = [
    path("register/", register_view, name="register"),
    path("login/", login_view, name="login"),
    path("token/", login_view, name="token_obtain"),
    path("token/refresh/", token_refresh_view, name="token_refresh"),
    path("profile/", profile_view, name="profile"),
    path("profile/change-password/", change_password_view, name="change-password"),
]
