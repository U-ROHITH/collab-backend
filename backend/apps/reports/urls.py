from django.urls import path

from apps.reports import views

urlpatterns = [
    path("overview/", views.ReportsOverviewView.as_view()),
    path("analytics/", views.ReportsOverviewView.as_view()),
    path("export/orders/", views.ExportOrdersCsvView.as_view()),
    path("export/campaigns/", views.ExportCampaignsCsvView.as_view()),
    path("export/transactions/", views.ExportTransactionsCsvView.as_view()),
]
