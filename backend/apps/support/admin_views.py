from uuid import UUID

from django.db.models import Case, F, IntegerField, Q, Value, When
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import NotFound
from apps.identity.models import AdminRole
from apps.identity.permissions import RequireAdminRoles
from apps.support import services
from apps.support.models import (
    SupportTicket,
    TicketPriority,
    TicketStatus,
    TicketTopic,
)
from apps.support.serializers import (
    AdminAssignInput,
    AdminNoteInput,
    AdminReplyInput,
    AdminSupportTicketDetailOutput,
    AdminTicketPatchInput,
    SupportMessageOutput,
    SupportTicketOutput,
)


class AdminTicketListView(APIView):
    """GET /v1/admin/support/tickets/ — list support tickets with filters, counts, and search."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="Status filter", required=False),
            OpenApiParameter("topic", str, description="Topic filter", required=False),
            OpenApiParameter("role", str, description="User role filter (brand, creator)", required=False),
            OpenApiParameter("priority", str, description="Priority filter", required=False),
            OpenApiParameter("assignee", str, description="'me', 'none', or admin UUID", required=False),
            OpenApiParameter("q", str, description="Search ticket number, subject, user", required=False),
            OpenApiParameter("limit", int, description="Page limit", required=False),
            OpenApiParameter("cursor", str, description="Cursor for pagination", required=False),
        ],
        responses={200: SupportTicketOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        base_qs = SupportTicket.objects.select_related("user", "assignee", "order")

        counts = {
            "open": base_qs.filter(status=TicketStatus.OPEN).count(),
            "waitingOnUser": base_qs.filter(status=TicketStatus.WAITING_ON_USER).count(),
            "resolved": base_qs.filter(status=TicketStatus.RESOLVED).count(),
            "closed": base_qs.filter(status=TicketStatus.CLOSED).count(),
            "total": base_qs.count(),
        }

        qs = base_qs

        status_param = request.query_params.get("status")
        if status_param and status_param in TicketStatus.values:
            qs = qs.filter(status=status_param)

        topic = request.query_params.get("topic")
        if topic and topic in TicketTopic.values:
            qs = qs.filter(topic=topic)

        role = request.query_params.get("role")
        if role:
            qs = qs.filter(role=role)

        priority = request.query_params.get("priority")
        if priority and priority in TicketPriority.values:
            qs = qs.filter(priority=priority)

        assignee = request.query_params.get("assignee")
        if assignee:
            if assignee.lower() == "me":
                qs = qs.filter(assignee=request.user)
            elif assignee.lower() == "none":
                qs = qs.filter(assignee__isnull=True)
            else:
                try:
                    qs = qs.filter(assignee_id=UUID(assignee))
                except (ValueError, TypeError):
                    pass

        q = request.query_params.get("q")
        if q:
            q_clean = q.strip()
            num_search = None
            if q_clean.upper().startswith("LL-") and q_clean[3:].isdigit():
                num_search = int(q_clean[3:])
            elif q_clean.isdigit():
                num_search = int(q_clean)

            filter_expr = (
                Q(subject__icontains=q_clean)
                | Q(user__email__icontains=q_clean)
                | Q(user__creator__display_name__icontains=q_clean)
                | Q(user__brands__name__icontains=q_clean)
            )
            if num_search is not None:
                filter_expr |= Q(number=num_search)

            qs = qs.filter(filter_expr).distinct()

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        # Ordered: open & waiting_on_user first, higher priority first, longest waiting first
        qs = qs.annotate(
            status_order=Case(
                When(status=TicketStatus.OPEN, then=Value(1)),
                When(status=TicketStatus.WAITING_ON_USER, then=Value(2)),
                When(status=TicketStatus.RESOLVED, then=Value(3)),
                When(status=TicketStatus.CLOSED, then=Value(4)),
                default=Value(5),
                output_field=IntegerField(),
            ),
            priority_order=Case(
                When(priority=TicketPriority.URGENT, then=Value(1)),
                When(priority=TicketPriority.HIGH, then=Value(2)),
                When(priority=TicketPriority.NORMAL, then=Value(3)),
                When(priority=TicketPriority.LOW, then=Value(4)),
                default=Value(5),
                output_field=IntegerField(),
            ),
        ).order_by("status_order", "priority_order", F("last_user_message_at").asc(nulls_last=True), "created_at")

        total_matching = qs.count()
        items = list(qs[:limit])
        serialized_data = SupportTicketOutput(items, many=True, context={"request": request}).data

        return Response(
            {
                "success": True,
                "data": serialized_data,
                "error": None,
                "meta": {
                    "counts": counts,
                    "total": total_matching,
                    "limit": limit,
                },
            }
        )


class AdminTicketDetailView(APIView):
    """GET /v1/admin/support/tickets/{id}/ — full ticket details including internal notes & user summary.
    PATCH /v1/admin/support/tickets/{id}/ — update status or priority.
    """

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]
    throttle_scope = "writes"

    @extend_schema(responses={200: AdminSupportTicketDetailOutput})
    def get(self, request: Request, ticket_id: UUID) -> Response:
        ticket = (
            SupportTicket.objects.select_related("user", "assignee", "order", "order__brand", "order__creator")
            .prefetch_related("messages")
            .filter(pk=ticket_id)
            .first()
        )
        if not ticket:
            raise NotFound("Ticket not found.")

        return Response(AdminSupportTicketDetailOutput(ticket, context={"request": request}).data)

    @extend_schema(request=AdminTicketPatchInput, responses={200: AdminSupportTicketDetailOutput})
    def patch(self, request: Request, ticket_id: UUID) -> Response:
        from apps.core.exceptions import Forbidden

        if getattr(request.user, "admin_role", None) not in (AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY):
            raise Forbidden("You do not have permission to modify tickets.", code="FORBIDDEN")

        data = AdminTicketPatchInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        ticket = services.admin_patch_ticket(
            admin_user=request.user,
            ticket_id=ticket_id,
            priority=v.get("priority"),
            status=v.get("status"),
        )
        return Response(AdminSupportTicketDetailOutput(ticket, context={"request": request}).data)


class AdminTicketReplyView(APIView):
    """POST /v1/admin/support/tickets/{id}/reply/ — reply to ticket
    (accessible to super_admin, trust_safety, finance).
    """

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]
    throttle_scope = "writes"

    @extend_schema(request=AdminReplyInput, responses={201: SupportMessageOutput})
    def post(self, request: Request, ticket_id: UUID) -> Response:
        data = AdminReplyInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        msg = services.admin_reply_ticket(
            admin_user=request.user,
            ticket_id=ticket_id,
            message=v["message"],
            attachment_upload_ids=v.get("attachmentUploadIds"),
        )
        return Response(
            SupportMessageOutput(msg, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AdminTicketNoteView(APIView):
    """POST /v1/admin/support/tickets/{id}/notes/ — add internal note (super_admin & trust_safety only; finance 403)."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]
    throttle_scope = "writes"

    @extend_schema(request=AdminNoteInput, responses={201: SupportMessageOutput})
    def post(self, request: Request, ticket_id: UUID) -> Response:
        data = AdminNoteInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        msg = services.admin_add_note(
            admin_user=request.user,
            ticket_id=ticket_id,
            note=v["note"],
        )
        return Response(
            SupportMessageOutput(msg, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AdminTicketAssignView(APIView):
    """POST /v1/admin/support/tickets/{id}/assign/ — assign ticket (super_admin & trust_safety only; finance 403)."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]
    throttle_scope = "writes"

    @extend_schema(request=AdminAssignInput, responses={200: AdminSupportTicketDetailOutput})
    def post(self, request: Request, ticket_id: UUID) -> Response:
        data = AdminAssignInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        ticket = services.admin_assign_ticket(
            admin_user=request.user,
            ticket_id=ticket_id,
            assignee_raw=v.get("adminId"),
        )
        return Response(AdminSupportTicketDetailOutput(ticket, context={"request": request}).data)


class AdminTicketPatchView(APIView):
    """PATCH /v1/admin/support/tickets/{id}/ — update status or priority
    (super_admin & trust_safety only; finance 403).
    """

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]
    throttle_scope = "writes"

    @extend_schema(request=AdminTicketPatchInput, responses={200: AdminSupportTicketDetailOutput})
    def patch(self, request: Request, ticket_id: UUID) -> Response:
        data = AdminTicketPatchInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        ticket = services.admin_patch_ticket(
            admin_user=request.user,
            ticket_id=ticket_id,
            priority=v.get("priority"),
            status=v.get("status"),
        )
        return Response(AdminSupportTicketDetailOutput(ticket, context={"request": request}).data)
