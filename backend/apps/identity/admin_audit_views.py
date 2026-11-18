import csv
import io
import json
from uuid import UUID

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record_audit, verify_audit_chain
from apps.identity.models import AdminRole, AuditLog
from apps.identity.permissions import RequireAdminRoles


class AdminAuditLogItemOutput(serializers.ModelSerializer):
    actorEmail = serializers.SerializerMethodField()  # noqa: N815
    actorId = serializers.UUIDField(source="actor_id", allow_null=True)  # noqa: N815
    entityType = serializers.CharField(source="entity_type")  # noqa: N815
    entityId = serializers.CharField(source="entity_id")  # noqa: N815
    detail = serializers.JSONField(source="metadata")
    prevHash = serializers.CharField(source="prev_hash")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "action",
            "actorId",
            "actorEmail",
            "entityType",
            "entityId",
            "detail",
            "hash",
            "prevHash",
            "createdAt",
        )

    def get_actorEmail(self, obj: AuditLog) -> str:  # noqa: N802
        return obj.actor.email or obj.actor.phone if obj.actor else "system"


class AdminAuditVerifyOutput(serializers.Serializer):
    ok = serializers.BooleanField()
    count = serializers.IntegerField()
    brokenAt = serializers.CharField(allow_null=True)  # noqa: N815


@extend_schema(tags=["Admin - System"])
class AdminAuditLogListView(APIView):
    """List append-only audit log records with filters."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(
        parameters=[
            OpenApiParameter("action", str, description="Filter by action name", required=False),
            OpenApiParameter("entityType", str, description="Filter by entity type", required=False),
            OpenApiParameter("actorId", str, description="Filter by actor UUID", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 50, max: 200)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor ID", required=False),
        ],
        responses={200: AdminAuditLogItemOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        qs = AuditLog.objects.select_related("actor").all()

        act = request.query_params.get("action")
        if act:
            qs = qs.filter(action=act)

        e_type = request.query_params.get("entityType")
        if e_type:
            qs = qs.filter(entity_type=e_type)

        actor_id = request.query_params.get("actorId")
        if actor_id:
            try:
                qs = qs.filter(actor_id=UUID(actor_id))
            except (ValueError, TypeError):
                qs = qs.none()

        try:
            limit = min(max(int(request.query_params.get("limit", 50)), 1), 200)
        except (ValueError, TypeError):
            limit = 50

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                c_item = AuditLog.objects.filter(pk=UUID(cursor)).first()
                if c_item:
                    qs = qs.filter(created_at__lt=c_item.created_at)
            except (ValueError, TypeError):
                pass

        logs = list(qs.order_by("-created_at")[:limit])
        return Response(AdminAuditLogItemOutput(logs, many=True).data)


@extend_schema(tags=["Admin - System"])
class AdminAuditLogVerifyView(APIView):
    """Verify cryptographic integrity of the SHA-256 audit log hash chain."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY, AdminRole.FINANCE)]

    @extend_schema(responses={200: AdminAuditVerifyOutput})
    def get(self, request: Request) -> Response:
        ok, count, broken_at = verify_audit_chain()

        record_audit(
            admin=request.user,
            action="audit.verify",
            target="audit_log",
            detail={"ok": ok, "verifiedCount": count, "brokenAt": broken_at},
        )

        return Response({"ok": ok, "count": count, "brokenAt": broken_at})


@extend_schema(tags=["Admin - System"])
class AdminAuditLogExportView(APIView):
    """Export audit log to CSV format; logs audit.export."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN, AdminRole.FINANCE)]

    @extend_schema(responses={200: None})
    def get(self, request: Request) -> HttpResponse:
        qs = AuditLog.objects.select_related("actor").order_by("-created_at")

        act = request.query_params.get("action")
        if act:
            qs = qs.filter(action=act)

        e_type = request.query_params.get("entityType")
        if e_type:
            qs = qs.filter(entity_type=e_type)

        logs = list(qs[:1000])

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID",
            "Timestamp",
            "Actor Email",
            "Action",
            "Entity Type",
            "Entity ID",
            "Detail",
            "Hash",
            "Previous Hash",
        ])

        for log in logs:
            writer.writerow([
                str(log.id),
                log.created_at.isoformat() if log.created_at else "",
                log.actor.email or log.actor.phone if log.actor else "system",
                log.action,
                log.entity_type,
                log.entity_id,
                json.dumps(log.metadata),
                log.hash,
                log.prev_hash,
            ])

        record_audit(
            admin=request.user,
            action="audit.export",
            target="audit_log",
            detail={"count": len(logs), "filters": dict(request.query_params)},
        )

        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="audit-log.csv"'
        return response

