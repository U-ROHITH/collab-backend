from uuid import UUID

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brands.views import my_brand
from apps.identity.permissions import IsBrand, IsCreator
from apps.offers import services
from apps.offers.models import Offer
from apps.offers.serializers import OfferInput, OfferOutput, OfferResponseInput
from apps.orders.serializers import OrderOutput


class BrandOffersView(ListAPIView):
    permission_classes = [IsBrand]
    serializer_class = OfferOutput

    def get_throttles(self):  # type: ignore[no-untyped-def]
        self.throttle_scope = "offers" if self.request.method == "POST" else "writes"
        return super().get_throttles()

    def get_queryset(self):  # type: ignore[no-untyped-def]
        return Offer.objects.filter(brand__owner=self.request.user).select_related("brand", "creator")

    @extend_schema(request=OfferInput, responses={201: OfferOutput})
    def post(self, request: Request) -> Response:
        data = OfferInput(data=request.data)
        data.is_valid(raise_exception=True)
        offer = services.send_offer(brand=my_brand(request), data=data.validated_data)
        return Response(OfferOutput(offer).data, status=status.HTTP_201_CREATED)


class BrandOfferWithdrawView(APIView):
    permission_classes = [IsBrand]

    @extend_schema(request=None, responses=OfferOutput)
    def post(self, request: Request, offer_id: UUID) -> Response:
        return Response(OfferOutput(services.withdraw_offer(brand=my_brand(request), offer_id=offer_id)).data)


class CreatorOffersView(ListAPIView):
    permission_classes = [IsCreator]
    serializer_class = OfferOutput

    def get_queryset(self):  # type: ignore[no-untyped-def]
        qs = Offer.objects.filter(creator__user=self.request.user).select_related("brand", "creator")
        if s := self.request.query_params.get("status"):
            qs = qs.filter(status=s)
        return qs


class CreatorOfferRespondView(APIView):
    permission_classes = [IsCreator]
    throttle_scope = "writes"

    @extend_schema(
        request=OfferResponseInput,
        responses=inline_serializer("OfferResponse", {"offer": OfferOutput(), "order": OrderOutput(allow_null=True)}),
    )
    def patch(self, request: Request, offer_id: UUID) -> Response:
        data = OfferResponseInput(data=request.data)
        data.is_valid(raise_exception=True)
        offer, order = services.respond_to_offer(
            actor=request.user,
            creator=request.user.creator,
            offer_id=offer_id,
            accept=data.validated_data["status"] == "accepted",
        )
        return Response(
            {
                "offer": OfferOutput(offer).data,
                "order": OrderOutput(order, context={"request": request}).data if order else None,
            }
        )
