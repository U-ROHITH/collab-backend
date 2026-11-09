from django.urls import path

from apps.orders import views

urlpatterns = [
    path("orders/", views.OrderListView.as_view()),
    path("creators/me/orders/", views.OrderListView.as_view()),
    path("creators/me/earnings/", views.CreatorEarningsView.as_view()),
    path("orders/<uuid:order_id>/", views.OrderDetailView.as_view()),
    path("orders/<uuid:order_id>/deliverables/", views.DeliverablesView.as_view()),
    path("orders/<uuid:order_id>/revisions/", views.RevisionView.as_view()),
    path("orders/<uuid:order_id>/approve/", views.ApproveView.as_view()),
    path("orders/<uuid:order_id>/cancel/", views.CancelView.as_view()),
    path("orders/<uuid:order_id>/disputes/", views.DisputeView.as_view()),
    path("orders/<uuid:order_id>/review/", views.ReviewCreateView.as_view()),
    path("creators/<uuid:creator_id>/reviews/", views.CreatorReviewsView.as_view()),
    path("dev/orders/<uuid:order_id>/simulate-payment/", views.DevSimulatePaymentView.as_view()),
]
