from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brands.views import my_brand
from apps.campaigns import services
from apps.campaigns.models import Bid
from apps.campaigns.serializers import (
    BidInput,
    BidOutput,
    CampaignInput,
    CampaignOutput,
    CampaignSearchParams,
)
from apps.core.exceptions import NotFound
from apps.core.pagination import EnvelopeCursorPagination
from apps.identity.models import Role
from apps.identity.permissions import IsApprovedCreator, IsBrand, IsBrandOrCreator
from apps.orders.serializers import OrderOutput


class CampaignCursorPagination(EnvelopeCursorPagination):
    def get_ordering(self, request, queryset, view):
        sort_param = request.query_params.get("sort") or request.query_params.get("ordering")
        if sort_param:
            sort_map = {
                "closes_at": ("closes_at", "id"),
                "closesAt": ("closes_at", "id"),
                "-closes_at": ("-closes_at", "-id"),
                "-closesAt": ("-closes_at", "-id"),
                "budget_min": ("budget_min_paise", "id"),
                "budgetMin": ("budget_min_paise", "id"),
                "-budget_min": ("-budget_min_paise", "-id"),
                "-budgetMin": ("-budget_min_paise", "-id"),
                "budget_max": ("budget_max_paise", "id"),
                "budgetMax": ("budget_max_paise", "id"),
                "-budget_max": ("-budget_max_paise", "-id"),
                "-budgetMax": ("-budget_max_paise", "-id"),
                "budget": ("-budget_max_paise", "-id"),
                "-budget": ("budget_min_paise", "id"),
                "created_at": ("created_at", "id"),
                "createdAt": ("created_at", "id"),
                "-created_at": ("-created_at", "-id"),
                "-createdAt": ("-created_at", "-id"),
            }
            if sort_param in sort_map:
                return sort_map[sort_param]
        return self.ordering


class CampaignListCreateView(ListAPIView):
    """GET: brand -> its campaigns; approved creator -> open campaigns. POST: brand creates."""

    permission_classes = [IsBrandOrCreator]
    serializer_class = CampaignOutput
    pagination_class = CampaignCursorPagination
    throttle_scope = "writes"

    @extend_schema(parameters=[CampaignSearchParams], responses=CampaignOutput(many=True))
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self):  # type: ignore[no-untyped-def]
        params = CampaignSearchParams(data=self.request.query_params.dict())
        params.is_valid(raise_exception=True)
        validated = params.validated_data

        qs = services.campaigns_visible_to(
            self.request.user,
            status=validated.get("status"),
        ).select_related("brand")

        return services.filter_campaigns(qs, validated)

    @extend_schema(request=CampaignInput, responses={201: CampaignOutput})
    def post(self, request: Request) -> Response:
        if not IsBrand().has_permission(request, self):
            raise NotFound()
        data = CampaignInput(data=request.data)
        data.is_valid(raise_exception=True)
        campaign = services.create_campaign(brand=my_brand(request), data=data.validated_data)
        return Response(CampaignOutput(campaign).data, status=status.HTTP_201_CREATED)


class CampaignDetailView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(responses=CampaignOutput)
    def get(self, request: Request, campaign_id: UUID) -> Response:
        campaign = services.campaigns_visible_to(request.user).select_related("brand").filter(pk=campaign_id).first()
        if campaign is None:
            raise NotFound()
        return Response(CampaignOutput(campaign).data)

    @extend_schema(request=CampaignInput, responses=CampaignOutput)
    def patch(self, request: Request, campaign_id: UUID) -> Response:
        if not IsBrand().has_permission(request, self):
            raise NotFound()
        data = CampaignInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        campaign = services.update_campaign(brand=my_brand(request), campaign_id=campaign_id, data=data.validated_data)
        return Response(CampaignOutput(campaign).data)


class CampaignCloseView(APIView):
    permission_classes = [IsBrand]

    @extend_schema(request=None, responses=CampaignOutput)
    def post(self, request: Request, campaign_id: UUID) -> Response:
        return Response(CampaignOutput(services.close_campaign(brand=my_brand(request), campaign_id=campaign_id)).data)


class BidListCreateView(ListAPIView):
    """GET: brand -> all bids on its campaign; creator -> only its own bid. POST: approved creator bids."""

    permission_classes = [IsBrandOrCreator]
    serializer_class = BidOutput

    def get_throttles(self):  # type: ignore[no-untyped-def]
        self.throttle_scope = "bids" if self.request.method == "POST" else "writes"
        return super().get_throttles()

    def get_queryset(self):  # type: ignore[no-untyped-def]
        user = self.request.user
        qs = Bid.objects.select_related("creator").filter(campaign_id=self.kwargs["campaign_id"])
        if user.role == Role.BRAND:
            return qs.filter(campaign__brand__owner=user)
        return qs.filter(creator__user=user)

    @extend_schema(request=BidInput, responses={201: BidOutput})
    def post(self, request: Request, campaign_id: UUID) -> Response:
        if not IsApprovedCreator().has_permission(request, self):
            raise NotFound()
        data = BidInput(data=request.data)
        data.is_valid(raise_exception=True)
        bid = services.create_bid(creator=request.user.creator, campaign_id=campaign_id, data=data.validated_data)
        return Response(BidOutput(bid).data, status=status.HTTP_201_CREATED)


class BidDetailView(APIView):
    permission_classes = [IsApprovedCreator]
    throttle_scope = "writes"

    @extend_schema(request=BidInput, responses=BidOutput)
    def patch(self, request: Request, campaign_id: UUID, bid_id: UUID) -> Response:
        data = BidInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        bid = services.update_bid(
            creator=request.user.creator, campaign_id=campaign_id, bid_id=bid_id, data=data.validated_data
        )
        return Response(BidOutput(bid).data)


class BidWithdrawView(APIView):
    permission_classes = [IsApprovedCreator]

    @extend_schema(request=None, responses=BidOutput)
    def post(self, request: Request, campaign_id: UUID, bid_id: UUID) -> Response:
        bid = services.withdraw_bid(creator=request.user.creator, campaign_id=campaign_id, bid_id=bid_id)
        return Response(BidOutput(bid).data)


class BidSelectView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=None, responses={201: OrderOutput})
    def post(self, request: Request, campaign_id: UUID, bid_id: UUID) -> Response:
        order = services.select_bid(actor=request.user, brand=my_brand(request), campaign_id=campaign_id, bid_id=bid_id)
        return Response(OrderOutput(order, context={"request": request}).data, status=status.HTTP_201_CREATED)
