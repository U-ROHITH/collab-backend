from datetime import timedelta
from uuid import UUID

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brands.admin_serializers import (
    AdminBrandDetailOutput,
    AdminBrandListOutput,
)
from apps.brands.models import Brand
from apps.campaigns.models import Campaign
from apps.core.exceptions import NotFound
from apps.identity.models import AdminRole, UserStatus
from apps.identity.permissions import RequireAdminRoles
from apps.moderation.models import AbuseReport


@extend_schema(tags=["Admin - Users"])
class AdminBrandListView(APIView):
    """List brands with advanced admin filters (status, verified, postingSpike, hasReports) and counts."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, description="Search by name, legal name, owner email", required=False),
            OpenApiParameter("status", str, description="User status: active, suspended, deactivated", required=False),
            OpenApiParameter("verified", bool, description="Filter registered verified brands", required=False),
            OpenApiParameter(
                "postingSpike", bool, description="Filter brands with rapid campaign posting", required=False
            ),
            OpenApiParameter("hasReports", bool, description="Filter brands with reports", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID)", required=False),
        ],
        responses={200: AdminBrandListOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        base_qs = Brand.objects.select_related("owner")

        total_brands = base_qs.count()
        suspended_brands = base_qs.filter(owner__status=UserStatus.SUSPENDED).count()

        qs = base_qs

        # Search query
        q = request.query_params.get("q")
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(legal_name__icontains=q)
                | Q(owner__email__icontains=q)
            )

        # Status
        status_param = request.query_params.get("status")
        if status_param and status_param in UserStatus.values:
            qs = qs.filter(owner__status=status_param)

        # Verified
        verified_param = request.query_params.get("verified")
        if verified_param is not None:
            is_verified = str(verified_param).lower() in ("true", "1")
            qs = qs.filter(reg_verified=is_verified)

        # Has reports
        has_reports = request.query_params.get("hasReports")
        if has_reports is not None:
            reports_exist = str(has_reports).lower() in ("true", "1")
            report_subquery = AbuseReport.objects.filter(target_type="brand", target_id=OuterRef("id"))
            if reports_exist:
                qs = qs.annotate(has_rep=Exists(report_subquery)).filter(has_rep=True)
            else:
                qs = qs.annotate(has_rep=Exists(report_subquery)).filter(has_rep=False)

        # Posting spike filter
        posting_spike_param = request.query_params.get("postingSpike")
        if posting_spike_param is not None:
            now = timezone.now()
            thirty_days_ago = now - timedelta(days=30)
            want_spike = str(posting_spike_param).lower() in ("true", "1")
            # Collect brands with spike
            all_brands = list(qs)
            matching_ids = []
            for b in all_brands:
                recent_c = Campaign.objects.filter(brand=b, created_at__gte=thirty_days_ago).count()
                if recent_c >= 5:
                    total_c = Campaign.objects.filter(brand=b).count()
                    days = max(1, (now - b.created_at).days)
                    months = max(1.0, days / 30.0)
                    monthly_avg = total_c / months
                    has_spike = recent_c > 2.0 * monthly_avg
                else:
                    has_spike = False
                if has_spike == want_spike:
                    matching_ids.append(b.id)
            qs = qs.filter(id__in=matching_ids)

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_item = Brand.objects.filter(pk=UUID(cursor)).first()
                if cursor_item:
                    qs = qs.filter(created_at__lt=cursor_item.created_at)
            except (ValueError, TypeError):
                pass

        total_matching = qs.count()
        items = list(qs.order_by("-created_at")[:limit])
        serialized_data = AdminBrandListOutput(items, many=True).data

        return Response(
            {
                "success": True,
                "data": serialized_data,
                "error": None,
                "meta": {
                    "total": total_brands,
                    "suspended": suspended_brands,
                    "matching": total_matching,
                    "limit": limit,
                },
            }
        )


@extend_schema(tags=["Admin - Users"])
class AdminBrandDetailView(APIView):
    """Retrieve brand profile, campaign metrics, order statistics, reports, and admin history."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminBrandDetailOutput})
    def get(self, request: Request, id: UUID) -> Response:
        brand = Brand.objects.select_related("owner").filter(pk=id).first()
        if brand is None:
            raise NotFound("Brand not found.")
        return Response(AdminBrandDetailOutput(brand).data)
