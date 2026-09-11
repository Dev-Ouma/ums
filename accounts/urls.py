from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.UMSLoginView.as_view(), name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("signup/", views.signup, name="signup"),
    path("profile/", views.profile, name="profile"),
    path("data/export/", views.personal_data_export, name="personal_data_export"),
    path("profile/settings/", views.profile_settings, name="profile_settings"),
    path("profile/sessions/revoke-others/", views.revoke_other_sessions,
         name="revoke_other_sessions"),

    # Self-service credential recovery
    path("password/forgot/", views.password_reset_request, name="password_reset_request"),
    path("password/forgot/sent/", views.password_reset_done, name="password_reset_done"),
    path("password/reset/<str:token>/", views.password_reset_confirm,
         name="password_reset_confirm"),
    path("password/change-required/", views.password_change_required,
         name="password_change_required"),
]
