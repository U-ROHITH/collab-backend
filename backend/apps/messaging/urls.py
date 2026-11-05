from django.urls import path

from apps.messaging import views
from apps.realtime.views import RealtimeTicketView

urlpatterns = [
    path("conversations/", views.ConversationListCreateView.as_view()),
    path("conversations/<uuid:conversation_id>/", views.ConversationDetailView.as_view()),
    path("conversations/<uuid:conversation_id>/messages/", views.MessageListCreateView.as_view()),
    path("conversations/<uuid:conversation_id>/read/", views.ConversationReadView.as_view()),
    path("realtime/ticket/", RealtimeTicketView.as_view()),
]
