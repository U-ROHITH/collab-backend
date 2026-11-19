from django.urls import path

from apps.support.admin_views import (
    AdminTicketAssignView,
    AdminTicketDetailView,
    AdminTicketListView,
    AdminTicketNoteView,
    AdminTicketPatchView,
    AdminTicketReplyView,
)

app_name = "admin_support"

urlpatterns = [
    path("tickets/", AdminTicketListView.as_view(), name="admin-ticket-list"),
    path("tickets/<uuid:ticket_id>/", AdminTicketDetailView.as_view(), name="admin-ticket-detail"),
    path("tickets/<uuid:ticket_id>/reply/", AdminTicketReplyView.as_view(), name="admin-ticket-reply"),
    path("tickets/<uuid:ticket_id>/notes/", AdminTicketNoteView.as_view(), name="admin-ticket-note"),
    path("tickets/<uuid:ticket_id>/assign/", AdminTicketAssignView.as_view(), name="admin-ticket-assign"),
    path("tickets/<uuid:ticket_id>/update/", AdminTicketPatchView.as_view(), name="admin-ticket-update"),
    path("tickets/<uuid:ticket_id>/patch/", AdminTicketPatchView.as_view(), name="admin-ticket-patch"),
]
