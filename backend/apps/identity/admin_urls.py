from django.urls import include, path

urlpatterns = [
    path("auth/", include("apps.identity.admin_auth_urls")),
    path("users/", include("apps.identity.admin_user_urls")),
    path("verifications/", include("apps.kyc.admin_urls")),
    path("campaigns/", include("apps.campaigns.admin_urls")),
    path("moderation/", include("apps.moderation.admin_urls")),
    path("finance/", include("apps.money.admin_urls")),
    path("system/", include("apps.identity.admin_system_urls")),
    path("support/", include("apps.support.admin_urls")),
]
