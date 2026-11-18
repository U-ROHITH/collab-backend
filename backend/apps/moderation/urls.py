from django.urls import path

from apps.moderation import views

urlpatterns = [
    path("abuse-reports/", views.AbuseReportCreateView.as_view(), name="abuse_report_create"),
]

