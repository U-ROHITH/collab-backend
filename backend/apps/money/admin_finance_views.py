import csv
import io
from datetime import timedelta
from uuid import UUID

from django.db.models import Sum
from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record_audit
from apps.identity.models import AdminRole
from apps.identity.permissions import RequireAdminRoles
from apps.money.admin_serializers import (
    AdminFinanceSummaryOutput,
    AdminPayoutOutput,
    AdminPlatformRulesOutput,
)
from apps.money.models import PlatformRule, WithdrawalRequest
from apps.orders.admin_views import ESCROW_HELD_STATUSES
from apps.orders.models import Order, OrderStatus


@extend_schema(tags=["Admin - Finance"])
class AdminPlatformRulesView(APIView):
    """Get platform commission and gateway fee percentages."""

    permission_classes = [RequireAdminRoles(AdminRole.FINANCE, AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminPlatformRulesOutput})
    def get(self, request: Request) -> Response:
        rule = PlatformRule.objects.filter(key="default").first()
        if rule is None:
            return Response({"commissionPct": 10.0, "gatewayPct": 2.0})
        return Response({
            "commissionPct": float(rule.commission_pct),
            "gatewayPct": float(rule.gateway_pct),
        })


@extend_schema(tags=["Admin - Finance"])
class AdminFinanceSummaryView(APIView):
    """Financial summary with GMV, platform revenue, escrow held, and monthly breakdown."""

    permission_classes = [RequireAdminRoles(AdminRole.FINANCE, AdminRole.SUPER_ADMIN)]

    @extend_schema(
        parameters=[
            OpenApiParameter("months", int, description="Number of months (3 or 6, default: 3)", required=False),
        ],
        responses={200: AdminFinanceSummaryOutput},
    )
    def get(self, request: Request) -> Response:
        try:
            months_count = int(request.query_params.get("months", 3))
            if months_count not in (3, 6):
                months_count = 3
        except (ValueError, TypeError):
            months_count = 3

        now = timezone.now()
        start_date = now - timedelta(days=months_count * 30)

        # Completed orders
        completed = Order.objects.filter(status=OrderStatus.COMPLETED, completed_at__gte=start_date)
        total_gmv = completed.aggregate(s=Sum("subtotal_paise"))["s"] or 0
        total_revenue = completed.aggregate(s=Sum("platform_fee_paise"))["s"] or 0
        total_gst = completed.aggregate(s=Sum("gst_paise"))["s"] or 0
        total_tds = completed.aggregate(s=Sum("tds_paise"))["s"] or 0

        # Escrow currently held
        held = Order.objects.filter(status__in=ESCROW_HELD_STATUSES)
        escrow_held = held.aggregate(s=Sum("subtotal_paise"))["s"] or 0

        # Payouts processed in period
        payouts_completed = WithdrawalRequest.objects.filter(status="completed", processed_at__gte=start_date)
        payouts_processed = payouts_completed.aggregate(s=Sum("amount_paise"))["s"] or 0

        # Monthly breakdown
        monthly = []
        for i in range(months_count):
            m_start = (now.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
            # End of this month: first day of next month
            if m_start.month == 12:
                m_end = m_start.replace(year=m_start.year + 1, month=1, day=1)
            else:
                m_end = m_start.replace(month=m_start.month + 1, day=1)

            m_orders = Order.objects.filter(
                status=OrderStatus.COMPLETED, completed_at__gte=m_start, completed_at__lt=m_end
            )
            m_gmv = m_orders.aggregate(s=Sum("subtotal_paise"))["s"] or 0
            m_rev = m_orders.aggregate(s=Sum("platform_fee_paise"))["s"] or 0
            m_gst = m_orders.aggregate(s=Sum("gst_paise"))["s"] or 0
            m_tds = m_orders.aggregate(s=Sum("tds_paise"))["s"] or 0

            m_payouts = (
                WithdrawalRequest.objects.filter(status="completed", processed_at__gte=m_start, processed_at__lt=m_end)
                .aggregate(s=Sum("amount_paise"))["s"]
                or 0
            )

            monthly.append({
                "month": m_start.strftime("%Y-%m"),
                "gmvPaise": m_gmv,
                "revenuePaise": m_rev,
                "gstPaise": m_gst,
                "tdsPaise": m_tds,
                "payoutsPaise": m_payouts,
                "ordersCount": m_orders.count(),
            })

        return Response({
            "totalGmvPaise": total_gmv,
            "totalRevenuePaise": total_revenue,
            "totalGstPaise": total_gst,
            "totalTdsPaise": total_tds,
            "escrowHeldPaise": escrow_held,
            "payoutsProcessedPaise": payouts_processed,
            "months": months_count,
            "monthly": monthly,
        })


STATUS_MAP = {
    "paid": "completed",
    "completed": "completed",
    "scheduled": "pending",
    "pending": "pending",
    "processing": "processing",
    "cancelled": "rejected",
    "rejected": "rejected",
}


@extend_schema(tags=["Admin - Finance"])
class AdminPayoutListView(APIView):
    """List withdrawal requests and payouts with filters."""

    permission_classes = [RequireAdminRoles(AdminRole.FINANCE, AdminRole.SUPER_ADMIN)]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="paid, scheduled, processing, cancelled", required=False),
            OpenApiParameter("creatorId", str, description="Filter by creator UUID", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor ID", required=False),
        ],
        responses={200: AdminPayoutOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        qs = WithdrawalRequest.objects.select_related("creator", "creator__user").all()

        st = request.query_params.get("status")
        if st:
            db_status = STATUS_MAP.get(st.lower(), st)
            qs = qs.filter(status=db_status)

        c_id = request.query_params.get("creatorId")
        if c_id:
            try:
                qs = qs.filter(creator_id=UUID(c_id))
            except (ValueError, TypeError):
                qs = qs.none()

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except (ValueError, TypeError):
            limit = 20

        payouts = list(qs.order_by("-created_at")[:limit])
        return Response(AdminPayoutOutput(payouts, many=True).data)


@extend_schema(tags=["Admin - Finance"])
class AdminPayoutExportView(APIView):
    """Export payouts to CSV with masked payout methods; logs audit."""

    permission_classes = [RequireAdminRoles(AdminRole.FINANCE, AdminRole.SUPER_ADMIN)]

    @extend_schema(responses={200: None})
    def get(self, request: Request) -> HttpResponse:
        qs = WithdrawalRequest.objects.select_related("creator", "creator__user").all()

        st = request.query_params.get("status")
        if st:
            db_status = STATUS_MAP.get(st.lower(), st)
            qs = qs.filter(status=db_status)

        payouts = list(qs.order_by("-created_at"))

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Payout ID",
            "Creator ID",
            "Creator Name",
            "Creator Handle",
            "Amount (INR)",
            "Status",
            "Payout Method",
            "Created At",
            "Processed At",
        ])

        for p in payouts:
            details = p.payout_details or {}
            method = p.payout_method or "bank_transfer"
            if method == "upi":
                vpa = details.get("vpa", "")
                masked_method = f"upi: ***@{vpa.split('@')[1]}" if "@" in vpa else "upi: ***"
            else:
                acc = details.get("account_number", "")
                masked_method = f"bank: ***{acc[-4:]}" if len(acc) >= 4 else "bank: ***"

            writer.writerow([
                str(p.id),
                str(p.creator_id),
                p.creator.display_name if p.creator else "",
                p.creator.slug if p.creator else "",
                f"{p.amount_paise / 100.0:.2f}",
                p.status,
                masked_method,
                p.created_at.isoformat() if p.created_at else "",
                p.processed_at.isoformat() if p.processed_at else "",
            ])

        record_audit(
            admin=request.user,
            action="payouts.export",
            target="finance:payouts",
            detail={"count": len(payouts), "filters": dict(request.query_params)},
        )

        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="payouts.csv"'
        return response
