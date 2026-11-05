from django.urls import path

from apps.notifications import views

urlpatterns = [
    path("notifications/", views.NotificationListView.as_view()),
    path("notifications/unread-count/", views.UnreadCountView.as_view()),
    path("notifications/read-all/", views.NotificationReadAllView.as_view()),
    path("notifications/<uuid:notification_id>/read/", views.NotificationReadView.as_view()),
]
