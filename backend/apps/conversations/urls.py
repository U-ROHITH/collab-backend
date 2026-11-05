from django.urls import path

from apps.conversations import views

urlpatterns = [
    path("conversations/", views.ConversationListCreateView.as_view(), name="conversation-list-create"),
    path(
        "conversations/<uuid:conversation_id>/messages/",
        views.ConversationMessagesView.as_view(),
        name="conversation-messages",
    ),
]
