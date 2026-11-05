from uuid import UUID

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import NotFound
from apps.creators.selectors import get_public_creator
from apps.identity.permissions import IsBrand, IsBrandOrCreator, IsCreator
from apps.orders import services
from apps.orders.models import Review
from apps.orders.selectors import orders_visible_to
from apps.orders.serializers import (
    DeliverableInput,
    DeliverableOutput,
    FeedbackInput,
    OrderListParams,
    OrderOutput,
    ReasonInput,
    ReviewInput,
    ReviewOutput,
)


def _out(order, request: Request) -> dict:  # type: ignore[no-untyped-def]
    return OrderOutput(order, context={"request": request}).data


def _visible_order(request: Request, order_id: UUID):  # type: ignore[no-untyped-def]
    order = orders_visible_to(request.user).filter(pk=order_id).first()
    if order is None:
        raise NotFound()
    return order


class OrderListView(ListAPIView):
    """Brand: orders it bought. Creator: orders it fulfils. Also served at /creators/me/orders/."""

    permission_classes = [IsBrandOrCreator]
    serializer_class = OrderOutput

    @extend_schema(parameters=[OrderListParams])
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self):  # type: ignore[no-untyped-def]
        params = OrderListParams(data=self.request.query_params.dict())
        params.is_valid(raise_exception=True)
        qs = orders_visible_to(self.request.user)
        if s := params.validated_data.get("status"):
            qs = qs.filter(status=s)
        return qs


class OrderDetailView(APIView):
    permission_classes = [IsBrandOrCreator]

    @extend_schema(responses=OrderOutput)
    def get(self, request: Request, order_id: UUID) -> Response:
        return Response(_out(_visible_order(request, order_id), request))


class DeliverablesView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(responses=DeliverableOutput(many=True))
    def get(self, request: Request, order_id: UUID) -> Response:
        order = _visible_order(request, order_id)
        return Response(DeliverableOutput(order.deliverable_versions.all(), many=True).data)

    @extend_schema(request=DeliverableInput, responses={201: DeliverableOutput})
    def post(self, request: Request, order_id: UUID) -> Response:
        if not IsCreator().has_permission(request, self):
            raise NotFound()
        data = DeliverableInput(data=request.data)
        data.is_valid(raise_exception=True)
        deliverable = services.submit_deliverable(
            actor=request.user,
            order_id=order_id,
            content_url=data.validated_data["contentUrl"],
            note=data.validated_data["note"],
        )
        return Response(DeliverableOutput(deliverable).data, status=status.HTTP_201_CREATED)


class RevisionView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=FeedbackInput, responses=OrderOutput)
    def post(self, request: Request, order_id: UUID) -> Response:
        data = FeedbackInput(data=request.data)
        data.is_valid(raise_exception=True)
        order = services.request_revision(
            actor=request.user, order_id=order_id, feedback=data.validated_data["feedback"]
        )
        return Response(_out(order, request))


class ApproveView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=None, responses=OrderOutput)
    def post(self, request: Request, order_id: UUID) -> Response:
        return Response(_out(services.approve(actor=request.user, order_id=order_id), request))


class CancelView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=ReasonInput, responses=OrderOutput)
    def post(self, request: Request, order_id: UUID) -> Response:
        data = ReasonInput(data=request.data)
        data.is_valid(raise_exception=True)
        order = services.cancel(actor=request.user, order_id=order_id, reason=data.validated_data["reason"])
        return Response(_out(order, request))


class DisputeView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=ReasonInput, responses=OrderOutput)
    def post(self, request: Request, order_id: UUID) -> Response:
        data = ReasonInput(data=request.data)
        data.is_valid(raise_exception=True)
        dispute = services.open_dispute(actor=request.user, order_id=order_id, reason=data.validated_data["reason"])
        return Response(_out(dispute.order, request), status=status.HTTP_201_CREATED)


class ReviewCreateView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=ReviewInput, responses={201: ReviewOutput})
    def post(self, request: Request, order_id: UUID) -> Response:
        data = ReviewInput(data=request.data)
        data.is_valid(raise_exception=True)
        review = services.create_review(actor=request.user, order_id=order_id, **data.validated_data)
        return Response(ReviewOutput(review).data, status=status.HTTP_201_CREATED)


class CreatorReviewsView(ListAPIView):
    permission_classes = [IsBrandOrCreator]
    serializer_class = ReviewOutput

    def get_queryset(self):  # type: ignore[no-untyped-def]
        creator = get_public_creator(self.kwargs["creator_id"])
        if creator is None:
            raise NotFound()
        return Review.objects.filter(creator=creator).select_related("brand")


class _DevEndpointsEnabled(BasePermission):
    def has_permission(self, request: Request, view: object) -> bool:
        return bool(settings.DEV_ENDPOINTS_ENABLED and settings.DEBUG)


class DevSimulatePaymentView(APIView):
    """DEV ONLY (DEBUG + DEV_ENDPOINTS_ENABLED): mark an order funded until Razorpay is wired in."""

    permission_classes = [_DevEndpointsEnabled, IsBrand]

    @extend_schema(request=None, responses=OrderOutput)
    def post(self, request: Request, order_id: UUID) -> Response:
        _visible_order(request, order_id)
        return Response(_out(services.system_event(order_id=order_id, event="fund", reason="dev simulate"), request))
