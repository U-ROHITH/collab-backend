from uuid import UUID

from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status as http_status
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, NotFound
from apps.identity.models import AdminRole
from apps.identity.permissions import RequireAdminRoles
from apps.money.models import CreatorWallet, WalletTransaction, WithdrawalRequest
from apps.notifications.services import notify
from apps.orders.admin_serializers import (
    AdminDisputeDetailOutput,
    AdminDisputeListOutput,
    AdminDisputeResolveInput,
    AdminDisputeResolveResult,
    AdminEscrowActionInput,
    AdminEscrowActionResult,
    AdminOrderDetailOutput,
    AdminOrderListOutput,
    AdminOrderPipelineOutput,
    AdminReviewActionResult,
    AdminReviewKeepInput,
    AdminReviewOutput,
    AdminReviewReasonInput,
)
from apps.orders.models import Dispute, DisputeStatus, IdempotencyRecord, Order, OrderStatus, Review, ReviewStatus
from apps.orders.state_machine import force_refund_order, force_release_order, restore_order_status

ESCROW_HELD_STATUSES = (
    OrderStatus.FUNDED,
    OrderStatus.DRAFT_SUBMITTED,
    OrderStatus.REVISION_REQUESTED,
    OrderStatus.APPROVED,
    OrderStatus.DISPUTED,
)


@extend_schema(tags=["Admin - Moderation"])
class AdminReviewListView(APIView):
    """List customer reviews with status filters and moderation counts."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="Review status: visible, flagged, removed", required=False),
            OpenApiParameter("creatorId", str, description="Filter by creator UUID", required=False),
            OpenApiParameter("brandId", str, description="Filter by brand UUID", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor (optional ID)", required=False),
        ],
        responses={200: AdminReviewOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        base_qs = Review.objects.select_related("brand", "creator")

        creator_id = request.query_params.get("creatorId")
        if creator_id:
            try:
                base_qs = base_qs.filter(creator_id=UUID(creator_id))
            except (ValueError, TypeError):
                base_qs = base_qs.none()

        brand_id = request.query_params.get("brandId")
        if brand_id:
            try:
                base_qs = base_qs.filter(brand_id=UUID(brand_id))
            except (ValueError, TypeError):
                base_qs = base_qs.none()

        counts = {
            "visible": base_qs.filter(status=ReviewStatus.VISIBLE).count(),
            "flagged": base_qs.filter(status=ReviewStatus.FLAGGED).count(),
            "removed": base_qs.filter(status=ReviewStatus.REMOVED).count(),
            "total": base_qs.count(),
        }

        qs = base_qs
        status_param = request.query_params.get("status")
        if status_param and status_param in ReviewStatus.values:
            qs = qs.filter(status=status_param)

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except ValueError:
            limit = 20

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_item = Review.objects.filter(pk=UUID(cursor)).first()
                if cursor_item:
                    qs = qs.filter(created_at__lt=cursor_item.created_at)
            except (ValueError, TypeError):
                pass

        total_matching = qs.count()
        items = list(qs.order_by("-created_at")[:limit])
        serialized_data = AdminReviewOutput(items, many=True).data

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


@extend_schema(tags=["Admin - Moderation"])
class AdminReviewDeleteView(APIView):
    """Mark a review as removed and recalculate creator rating."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminReviewReasonInput, responses={200: AdminReviewActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminReviewReasonInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            review = (
                Review.objects.select_for_update(of=("self",))
                .select_related("creator", "brand")
                .filter(pk=id)
                .first()
            )
            if review is None:
                raise NotFound("Review not found.")

            review.status = ReviewStatus.REMOVED
            review.flag_reason = reason
            review.save(update_fields=["status", "flag_reason", "updated_at"])

            # Recalculate creator rating from visible reviews
            creator = review.creator
            visible_reviews = Review.objects.filter(creator=creator, status=ReviewStatus.VISIBLE)
            creator.rating_count = visible_reviews.count()
            creator.rating_sum = sum(r.stars for r in visible_reviews)
            creator.save(update_fields=["rating_count", "rating_sum", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="review.delete",
                target=review,
                detail={"reason": reason, "creatorId": str(creator.id)},
            )

            return Response(
                {
                    "review": AdminReviewOutput(review).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Moderation"])
class AdminReviewKeepView(APIView):
    """Keep a flagged or removed review as visible and ensure rating is counted."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminReviewKeepInput, responses={200: AdminReviewActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        input_serializer = AdminReviewKeepInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        note = input_serializer.validated_data.get("note", "")

        with transaction.atomic():
            review = (
                Review.objects.select_for_update(of=("self",))
                .select_related("creator", "brand")
                .filter(pk=id)
                .first()
            )
            if review is None:
                raise NotFound("Review not found.")

            review.status = ReviewStatus.VISIBLE
            review.flag_reason = ""
            review.save(update_fields=["status", "flag_reason", "updated_at"])

            # Recalculate creator rating
            creator = review.creator
            visible_reviews = Review.objects.filter(creator=creator, status=ReviewStatus.VISIBLE)
            creator.rating_count = visible_reviews.count()
            creator.rating_sum = sum(r.stars for r in visible_reviews)
            creator.save(update_fields=["rating_count", "rating_sum", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="review.keep",
                target=review,
                detail={"note": note, "creatorId": str(creator.id)},
            )

            return Response(
                {
                    "review": AdminReviewOutput(review).data,
                    "audit": audit,
                }
            )


# --- Orders Pipeline & Escrow Controls ----------------------------------------------


@extend_schema(tags=["Admin - Finance"])
class AdminOrderListView(APIView):
    """List orders with filters across status, brand, creator, search query."""

    permission_classes = [RequireAdminRoles(AdminRole.FINANCE, AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="Filter by OrderStatus", required=False),
            OpenApiParameter("brandId", str, description="Filter by brand UUID", required=False),
            OpenApiParameter("creatorId", str, description="Filter by creator UUID", required=False),
            OpenApiParameter("q", str, description="Search order title, brand or creator name", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor ID", required=False),
        ],
        responses={200: AdminOrderListOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        qs = Order.objects.select_related("brand", "creator", "brand__owner", "creator__user").all()

        st = request.query_params.get("status")
        if st and st in OrderStatus.values:
            qs = qs.filter(status=st)

        b_id = request.query_params.get("brandId")
        if b_id:
            try:
                qs = qs.filter(brand_id=UUID(b_id))
            except (ValueError, TypeError):
                qs = qs.none()

        c_id = request.query_params.get("creatorId")
        if c_id:
            try:
                qs = qs.filter(creator_id=UUID(c_id))
            except (ValueError, TypeError):
                qs = qs.none()

        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(brand__name__icontains=q)
                | Q(creator__display_name__icontains=q)
            )

        total = qs.count()

        cursor = request.query_params.get("cursor")
        if cursor:
            try:
                cursor_order = Order.objects.filter(pk=UUID(cursor)).first()
                if cursor_order:
                    qs = qs.filter(created_at__lt=cursor_order.created_at)
            except (ValueError, TypeError):
                pass

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except (ValueError, TypeError):
            limit = 20

        orders = list(qs.order_by("-created_at")[:limit])
        data = AdminOrderListOutput(orders, many=True).data
        headers = {"X-Total-Count": str(total)}
        if len(orders) == limit:
            headers["X-Next-Cursor"] = str(orders[-1].id)

        return Response(data, headers=headers)


@extend_schema(tags=["Admin - Finance"])
class AdminOrderPipelineView(APIView):
    """Aggregate counts and values by order status, plus held escrow and disputed totals."""

    permission_classes = [RequireAdminRoles(AdminRole.FINANCE, AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminOrderPipelineOutput})
    def get(self, request: Request) -> Response:
        pipeline = []
        for st in OrderStatus.values:
            st_orders = Order.objects.filter(status=st)
            cnt = st_orders.count()
            val = st_orders.aggregate(s=Sum("subtotal_paise"))["s"] or 0
            pipeline.append({"status": st, "count": cnt, "value": val})

        held_orders = Order.objects.filter(status__in=ESCROW_HELD_STATUSES)
        held_total = held_orders.aggregate(s=Sum("subtotal_paise"))["s"] or 0

        disputed_orders = Order.objects.filter(status=OrderStatus.DISPUTED)
        disputed_total = disputed_orders.aggregate(s=Sum("subtotal_paise"))["s"] or 0

        return Response(
            {
                "pipeline": pipeline,
                "heldTotal": held_total,
                "disputedTotal": disputed_total,
            }
        )


@extend_schema(tags=["Admin - Finance"])
class AdminOrderDetailView(APIView):
    """Detailed order view with financial breakdown, payout state, and audit timeline."""

    permission_classes = [RequireAdminRoles(AdminRole.FINANCE, AdminRole.SUPER_ADMIN, AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminOrderDetailOutput})
    def get(self, request: Request, id: UUID) -> Response:
        order = (
            Order.objects.select_related("brand", "creator", "brand__owner", "creator__user")
            .filter(pk=id)
            .first()
        )
        if order is None:
            raise NotFound("Order not found.")
        return Response(AdminOrderDetailOutput(order).data)


@extend_schema(tags=["Admin - Finance"])
class AdminOrderForceReleaseView(APIView):
    """Force release escrow to creator (super_admin only, idempotent)."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN)]

    @extend_schema(request=AdminEscrowActionInput, responses={200: AdminEscrowActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        key = request.headers.get("Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY")
        if not key:
            raise ValidationError("Idempotency-Key header is required.")

        record = IdempotencyRecord.objects.filter(key=key).first()
        if record:
            return Response(record.response_body, status=record.response_code)

        inp = AdminEscrowActionInput(data=request.data)
        inp.is_valid(raise_exception=True)
        reason = inp.validated_data["reason"]
        note = inp.validated_data.get("note", "")
        dispute_id = inp.validated_data.get("disputeId")

        with transaction.atomic():
            order = (
                Order.objects.select_for_update(of=("self",))
                .select_related("brand", "creator", "brand__owner", "creator__user")
                .filter(pk=id)
                .first()
            )
            if order is None:
                raise NotFound("Order not found.")
            if order.status not in ESCROW_HELD_STATUSES:
                raise Conflict(
                    f"Escrow cannot be released for order in status '{order.status}'.",
                    code="INVALID_STATUS",
                )

            now = timezone.now()
            force_release_order(order, request.user)
            order.save(update_fields=["status", "completed_at", "released_at", "forced_by", "version", "updated_at"])

            # Credit creator wallet
            wallet, _ = CreatorWallet.objects.select_for_update().get_or_create(creator=order.creator)
            wallet.balance_paise += order.creator_payout_paise
            wallet.total_earned_paise += order.creator_payout_paise
            wallet.save(update_fields=["balance_paise", "total_earned_paise", "updated_at"])

            WalletTransaction.objects.create(
                wallet=wallet,
                order=order,
                type="credit_order",
                amount_paise=order.creator_payout_paise,
                balance_after_paise=wallet.balance_paise,
                description=f"Escrow force-released for order {order.title}",
            )

            # Record or set payout to processing
            w = (
                WithdrawalRequest.objects.select_for_update()
                .filter(creator=order.creator, status="pending")
                .first()
            )
            if w:
                w.status = "processing"
                w.save(update_fields=["status", "updated_at"])
            else:
                WithdrawalRequest.objects.create(
                    creator=order.creator,
                    wallet=wallet,
                    amount_paise=order.creator_payout_paise,
                    payout_method="bank_transfer",
                    status="processing",
                )

            # Resolve any open dispute on this order
            dispute_qs = Dispute.objects.select_for_update().filter(order=order, status=DisputeStatus.OPEN)
            if dispute_id:
                dispute_qs = dispute_qs.filter(pk=dispute_id)
            dispute = dispute_qs.first()
            dispute_resolved = False
            if dispute:
                dispute.status = DisputeStatus.RESOLVED
                dispute.resolution = f"Force-released by admin: {reason}"
                dispute.resolved_by = request.user
                dispute.resolved_at = now
                dispute.decision_metadata = {
                    "action": "force_release",
                    "forcedBy": str(request.user.id),
                    "reason": reason,
                }
                dispute.save(
                    update_fields=[
                        "status",
                        "resolution",
                        "resolved_by",
                        "resolved_at",
                        "decision_metadata",
                        "updated_at",
                    ]
                )
                dispute_resolved = True

            audit = record_audit(
                admin=request.user,
                action="escrow.force_release",
                target=order,
                detail={
                    "reason": reason,
                    "disputeId": str(dispute.id) if dispute else None,
                    "amountPaise": order.creator_payout_paise,
                    **({"note": note} if note else {}),
                },
            )

            # Notifications
            if order.brand and order.brand.owner_id:
                notify(
                    user_id=order.brand.owner_id,
                    type="order_completed",
                    title="Order Escrow Released by Admin",
                    body=f"Escrow for '{order.title}' has been released to the creator.",
                    data={"orderId": str(order.id)},
                )
            if order.creator and order.creator.user_id:
                notify(
                    user_id=order.creator.user_id,
                    type="order_completed",
                    title="Funds Released for Order",
                    body=f"Escrow for '{order.title}' has been released to your account.",
                    data={"orderId": str(order.id)},
                )

            resp_data = {
                "order": AdminOrderListOutput(order).data,
                "audit": audit,
                "disputeResolved": dispute_resolved,
            }

            IdempotencyRecord.objects.create(
                key=key,
                user=request.user,
                action="escrow.force_release",
                response_code=200,
                response_body=resp_data,
            )

            return Response(resp_data, status=http_status.HTTP_200_OK)


@extend_schema(tags=["Admin - Finance"])
class AdminOrderRefundView(APIView):
    """Refund order escrow back to brand (super_admin only, idempotent)."""

    permission_classes = [RequireAdminRoles(AdminRole.SUPER_ADMIN)]

    @extend_schema(request=AdminEscrowActionInput, responses={200: AdminEscrowActionResult})
    def post(self, request: Request, id: UUID) -> Response:
        key = request.headers.get("Idempotency-Key") or request.META.get("HTTP_IDEMPOTENCY_KEY")
        if not key:
            raise ValidationError("Idempotency-Key header is required.")

        record = IdempotencyRecord.objects.filter(key=key).first()
        if record:
            return Response(record.response_body, status=record.response_code)

        inp = AdminEscrowActionInput(data=request.data)
        inp.is_valid(raise_exception=True)
        reason = inp.validated_data["reason"]
        note = inp.validated_data.get("note", "")
        dispute_id = inp.validated_data.get("disputeId")

        with transaction.atomic():
            order = (
                Order.objects.select_for_update(of=("self",))
                .select_related("brand", "creator", "brand__owner", "creator__user")
                .filter(pk=id)
                .first()
            )
            if order is None:
                raise NotFound("Order not found.")
            if order.status not in ESCROW_HELD_STATUSES:
                raise Conflict(
                    f"Escrow cannot be refunded for order in status '{order.status}'.",
                    code="INVALID_STATUS",
                )

            now = timezone.now()
            force_refund_order(order, request.user)
            order.save(update_fields=["status", "refunded_at", "forced_by", "version", "updated_at"])

            # Cancel unpaid payouts
            WithdrawalRequest.objects.filter(creator=order.creator, status="pending").update(
                status="rejected",
                rejection_reason=f"Order refunded: {reason}",
            )

            # Resolve any open dispute on this order
            dispute_qs = Dispute.objects.select_for_update().filter(order=order, status=DisputeStatus.OPEN)
            if dispute_id:
                dispute_qs = dispute_qs.filter(pk=dispute_id)
            dispute = dispute_qs.first()
            dispute_resolved = False
            if dispute:
                dispute.status = DisputeStatus.RESOLVED
                dispute.resolution = f"Refunded by admin: {reason}"
                dispute.resolved_by = request.user
                dispute.resolved_at = now
                dispute.decision_metadata = {
                    "action": "refund",
                    "forcedBy": str(request.user.id),
                    "reason": reason,
                }
                dispute.save(
                    update_fields=[
                        "status",
                        "resolution",
                        "resolved_by",
                        "resolved_at",
                        "decision_metadata",
                        "updated_at",
                    ]
                )
                dispute_resolved = True

            audit = record_audit(
                admin=request.user,
                action="escrow.refund",
                target=order,
                detail={
                    "reason": reason,
                    "disputeId": str(dispute.id) if dispute else None,
                    "amountPaise": order.brand_total_paise,
                    **({"note": note} if note else {}),
                },
            )

            # Notifications
            if order.brand and order.brand.owner_id:
                notify(
                    user_id=order.brand.owner_id,
                    type="order_refunded",
                    title="Order Refund Processed",
                    body=f"A refund for order '{order.title}' has been processed.",
                    data={"orderId": str(order.id)},
                )
            if order.creator and order.creator.user_id:
                notify(
                    user_id=order.creator.user_id,
                    type="order_refunded",
                    title="Order Refunded to Brand",
                    body=f"Order '{order.title}' was refunded to the brand.",
                    data={"orderId": str(order.id)},
                )

            resp_data = {
                "order": AdminOrderListOutput(order).data,
                "audit": audit,
                "disputeResolved": dispute_resolved,
            }

            IdempotencyRecord.objects.create(
                key=key,
                user=request.user,
                action="escrow.refund",
                response_code=200,
                response_body=resp_data,
            )

            return Response(resp_data, status=http_status.HTTP_200_OK)


# --- Dispute Administration --------------------------------------------------------


@extend_schema(tags=["Admin - Finance"])
class AdminDisputeListView(APIView):
    """List disputes with open first, sorted by SLA due date."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY, AdminRole.SUPER_ADMIN, AdminRole.FINANCE)]

    @extend_schema(
        parameters=[
            OpenApiParameter("status", str, description="Filter by status: open | resolved", required=False),
            OpenApiParameter("kind", str, description="Filter by kind: dispute | payment_complaint", required=False),
            OpenApiParameter("limit", int, description="Page limit (default: 20, max: 100)", required=False),
            OpenApiParameter("cursor", str, description="Pagination cursor ID", required=False),
        ],
        responses={200: AdminDisputeListOutput(many=True)},
    )
    def get(self, request: Request) -> Response:
        qs = Dispute.objects.select_related("order", "order__brand", "order__creator", "opened_by").all()

        st = request.query_params.get("status")
        if st and st in DisputeStatus.values:
            qs = qs.filter(status=st)

        kd = request.query_params.get("kind")
        if kd:
            qs = qs.filter(kind=kd)

        # Open disputes first, then sorted by sla_due_at ascending
        qs = qs.order_by(
            models.Case(
                models.When(status=DisputeStatus.OPEN, then=0),
                default=1,
            ),
            "sla_due_at",
            "-created_at",
        )

        try:
            limit = min(max(int(request.query_params.get("limit", 20)), 1), 100)
        except (ValueError, TypeError):
            limit = 20

        disputes = list(qs[:limit])
        return Response(AdminDisputeListOutput(disputes, many=True).data)


@extend_schema(tags=["Admin - Finance"])
class AdminDisputeDetailView(APIView):
    """Detailed dispute view including evidence, statements, and admin history."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY, AdminRole.SUPER_ADMIN, AdminRole.FINANCE)]

    @extend_schema(responses={200: AdminDisputeDetailOutput})
    def get(self, request: Request, id: UUID) -> Response:
        dispute = (
            Dispute.objects.select_related(
                "order",
                "order__brand",
                "order__creator",
                "opened_by",
                "resolved_by",
            )
            .filter(pk=id)
            .first()
        )
        if dispute is None:
            raise NotFound("Dispute not found.")
        return Response(AdminDisputeDetailOutput(dispute).data)


@extend_schema(tags=["Admin - Finance"])
class AdminDisputeResolveView(APIView):
    """Resolve dispute without moving money, restoring order to status_before_dispute."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY, AdminRole.SUPER_ADMIN)]

    @extend_schema(request=AdminDisputeResolveInput, responses={200: AdminDisputeResolveResult})
    def post(self, request: Request, id: UUID) -> Response:
        inp = AdminDisputeResolveInput(data=request.data)
        inp.is_valid(raise_exception=True)
        reason = inp.validated_data["reason"]
        note = inp.validated_data.get("note", "")

        with transaction.atomic():
            dispute = (
                Dispute.objects.select_for_update(of=("self",))
                .select_related("order", "order__brand", "order__creator", "opened_by", "resolved_by")
                .filter(pk=id)
                .first()
            )
            if dispute is None:
                raise NotFound("Dispute not found.")
            if dispute.status == DisputeStatus.RESOLVED:
                raise Conflict("Dispute is already resolved.", code="ALREADY_RESOLVED")

            order = (
                Order.objects.select_for_update(of=("self",))
                .select_related("brand", "creator")
                .filter(pk=dispute.order_id)
                .first()
            )
            if order is None:
                raise NotFound("Order not found.")

            target_status = order.status_before_dispute or OrderStatus.FUNDED
            restore_order_status(order, target_status)
            order.save(update_fields=["status", "version", "updated_at"])

            now = timezone.now()
            dispute.status = DisputeStatus.RESOLVED
            dispute.resolution = reason
            dispute.resolved_by = request.user
            dispute.resolved_at = now
            dispute.decision_metadata = {
                "action": "resolve",
                "resolvedBy": str(request.user.id),
                "reason": reason,
                "restoredStatus": target_status,
                **({"note": note} if note else {}),
            }
            dispute.save(
                update_fields=["status", "resolution", "resolved_by", "resolved_at", "decision_metadata", "updated_at"]
            )

            audit_detail = {"reason": reason, "orderId": str(order.id), "restoredStatus": target_status}
            if note:
                audit_detail["note"] = note
            audit = record_audit(
                admin=request.user,
                action="dispute.resolve",
                target=dispute,
                detail=audit_detail,
            )

            # Notify both parties
            if order.brand and order.brand.owner_id:
                notify(
                    user_id=order.brand.owner_id,
                    type="dispute_resolved",
                    title=f"Dispute Resolved: {order.title}",
                    body=f"Dispute on '{order.title}' has been resolved: {reason}",
                    data={"orderId": str(order.id), "disputeId": str(dispute.id)},
                )
            if order.creator and order.creator.user_id:
                notify(
                    user_id=order.creator.user_id,
                    type="dispute_resolved",
                    title=f"Dispute Resolved: {order.title}",
                    body=f"Dispute on '{order.title}' has been resolved: {reason}",
                    data={"orderId": str(order.id), "disputeId": str(dispute.id)},
                )

            return Response(
                {
                    "dispute": AdminDisputeDetailOutput(dispute).data,
                    "order": AdminOrderListOutput(order).data,
                    "audit": audit,
                },
                status=http_status.HTTP_200_OK,
            )

