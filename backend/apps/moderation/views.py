from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import Conflict
from apps.moderation.models import AbuseReport, AbuseReportStatus
from apps.moderation.serializers import AbuseReportInput, AbuseReportOutput
from apps.moderation.services import resolve_target


class AbuseReportCreateView(APIView):
    """Submit an abuse or policy violation report against a creator, brand, campaign, or review."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "writes"

    @extend_schema(request=AbuseReportInput, responses={201: AbuseReportOutput})
    def post(self, request: Request) -> Response:
        input_serializer = AbuseReportInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        target_type = data["targetType"]
        target_id = data["targetId"]

        # Validate target exists and retrieve owner user
        _, target_user = resolve_target(target_type, target_id)

        # Enforce one report per user per target
        if AbuseReport.objects.filter(reporter=request.user, target_type=target_type, target_id=target_id).exists():
            raise Conflict("You have already reported this item.", code="ALREADY_REPORTED")

        report = AbuseReport.objects.create(
            reporter=request.user,
            target_type=target_type,
            target_id=target_id,
            target_user=target_user,
            reason=data["reason"],
            details=data.get("details", ""),
            status=AbuseReportStatus.PENDING,
        )

        return Response(AbuseReportOutput(report).data, status=status.HTTP_201_CREATED)

