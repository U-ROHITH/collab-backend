from django.urls import path

from apps.creators import views

urlpatterns = [
    path("creators/me/", views.CreatorMeView.as_view()),
    path("creators/me/packages/", views.MyPackagesView.as_view()),
    path("creators/me/packages/<uuid:package_id>/", views.MyPackageDetailView.as_view()),
    path("creators/", views.CreatorSearchView.as_view()),
    path("creators/<uuid:creator_id>/", views.CreatorDetailView.as_view()),
]
