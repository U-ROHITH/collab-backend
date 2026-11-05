from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.views import healthz, readyz

v1 = [
    path("auth/", include("apps.identity.urls")),
    path("", include("apps.brands.urls")),
    path("", include("apps.creators.urls")),
    path("", include("apps.campaigns.urls")),
    path("", include("apps.offers.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.notifications.urls")),
    path("", include("apps.uploads.urls")),
    path("", include("apps.messaging.urls")),
]

urlpatterns = [
    path("healthz", healthz),
    path("readyz", readyz),
    path(settings.ADMIN_URL, admin.site.urls),
    path("v1/", include(v1)),
]

if settings.DEBUG:
    urlpatterns += [
        path("v1/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    ]
