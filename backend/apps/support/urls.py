from django.urls import path

from apps.support.views import (
    TicketDetailView,
    TicketListCreateView,
    TicketMessageCreateView,
    TicketReopenView,
    TicketResolveView,
)

app_name = "support"

urlpatterns = [
    path("tickets/", TicketListCreateView.as_view(), name="ticket-list-create"),
    path("tickets/<uuid:ticket_id>/", TicketDetailView.as_view(), name="ticket-detail"),
    path("tickets/<uuid:ticket_id>/messages/", TicketMessageCreateView.as_view(), name="ticket-message-create"),
    path("tickets/<uuid:ticket_id>/resolve/", TicketResolveView.as_view(), name="ticket-resolve"),
    path("tickets/<uuid:ticket_id>/reopen/", TicketReopenView.as_view(), name="ticket-reopen"),
]
