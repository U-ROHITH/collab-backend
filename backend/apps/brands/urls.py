from django.urls import path

from apps.brands import views

urlpatterns = [
    path("brands/me/", views.BrandMeView.as_view()),
    path("brands/<uuid:brand_id>/", views.BrandPublicProfileView.as_view()),
    path("lists/", views.ListsView.as_view()),
    path("lists/<uuid:list_id>/", views.ListDetailView.as_view()),
    path("lists/<uuid:list_id>/creators/", views.ListCreatorsView.as_view()),
    path("lists/<uuid:list_id>/creators/<uuid:creator_id>/", views.ListCreatorDetailView.as_view()),
    path("cart/", views.CartView.as_view()),
    path("cart/<uuid:item_id>/", views.CartItemView.as_view()),
    path("cart/checkout/", views.CheckoutView.as_view()),
]
