from uuid import UUID

from django.db.models import Count
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.brands import services
from apps.brands.models import Brand, BrandList, CartItem
from apps.brands.serializers import (
    BrandInput,
    BrandOutput,
    CartAddInput,
    CartItemOutput,
    CheckoutInput,
    ListAddCreatorInput,
    ListDetailOutput,
    ListInput,
    ListOutput,
)
from apps.core.exceptions import NotFound
from apps.core.parties import brand_public
from apps.identity.permissions import IsBrand, IsBrandOrCreator
from apps.orders.serializers import OrderOutput


def my_brand(request: Request) -> Brand:
    brand = getattr(request.user, "brand", None)
    if brand is None:
        raise NotFound("Brand profile not found.")
    return brand


class BrandMeView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(responses=BrandOutput)
    def get(self, request: Request) -> Response:
        return Response(BrandOutput(my_brand(request)).data)

    @extend_schema(request=BrandInput, responses=BrandOutput)
    def patch(self, request: Request) -> Response:
        data = BrandInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        return Response(BrandOutput(services.update_brand(brand=my_brand(request), data=data.validated_data)).data)


class ListsView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(responses=ListOutput(many=True))
    def get(self, request: Request) -> Response:
        qs = BrandList.objects.filter(brand=my_brand(request)).annotate(creator_count=Count("items"))
        return Response(ListOutput(qs, many=True).data)

    @extend_schema(request=ListInput, responses={201: ListOutput})
    def post(self, request: Request) -> Response:
        data = ListInput(data=request.data)
        data.is_valid(raise_exception=True)
        obj = services.create_list(brand=my_brand(request), name=data.validated_data["name"])
        obj.creator_count = 0
        return Response(ListOutput(obj).data, status=status.HTTP_201_CREATED)


class ListDetailView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(responses=ListDetailOutput)
    def get(self, request: Request, list_id: UUID) -> Response:
        obj = BrandList.objects.filter(pk=list_id, brand=my_brand(request)).first()
        if obj is None:
            raise NotFound()
        return Response(ListDetailOutput(obj).data)

    @extend_schema(request=ListInput, responses=ListDetailOutput)
    def patch(self, request: Request, list_id: UUID) -> Response:
        data = ListInput(data=request.data)
        data.is_valid(raise_exception=True)
        obj = services.rename_list(brand=my_brand(request), list_id=list_id, name=data.validated_data["name"])
        return Response(ListDetailOutput(obj).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, list_id: UUID) -> Response:
        services.delete_list(brand=my_brand(request), list_id=list_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListCreatorsView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=ListAddCreatorInput, responses={204: None})
    def post(self, request: Request, list_id: UUID) -> Response:
        data = ListAddCreatorInput(data=request.data)
        data.is_valid(raise_exception=True)
        services.add_creator_to_list(
            brand=my_brand(request), list_id=list_id, creator_id=data.validated_data["creatorId"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ListCreatorDetailView(APIView):
    permission_classes = [IsBrand]

    @extend_schema(responses={204: None})
    def delete(self, request: Request, list_id: UUID, creator_id: UUID) -> Response:
        services.remove_creator_from_list(brand=my_brand(request), list_id=list_id, creator_id=creator_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CartView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(responses=CartItemOutput(many=True))
    def get(self, request: Request) -> Response:
        items = CartItem.objects.filter(brand=my_brand(request)).select_related("package__creator")
        return Response(CartItemOutput(items, many=True).data)

    @extend_schema(request=CartAddInput, responses={201: CartItemOutput})
    def post(self, request: Request) -> Response:
        data = CartAddInput(data=request.data)
        data.is_valid(raise_exception=True)
        item = services.add_to_cart(
            brand=my_brand(request), package_id=data.validated_data["packageId"], brief=data.validated_data["brief"]
        )
        return Response(CartItemOutput(item).data, status=status.HTTP_201_CREATED)


class CartItemView(APIView):
    permission_classes = [IsBrand]

    @extend_schema(responses={204: None})
    def delete(self, request: Request, item_id: UUID) -> Response:
        services.remove_from_cart(brand=my_brand(request), item_id=item_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CheckoutView(APIView):
    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=CheckoutInput, responses={201: OrderOutput(many=True)})
    def post(self, request: Request) -> Response:
        data = CheckoutInput(data=request.data)
        data.is_valid(raise_exception=True)
        orders = services.checkout_cart(
            brand=my_brand(request), actor=request.user, usage_rights=data.validated_data["usageRights"]
        )
        return Response(OrderOutput(orders, many=True, context={"request": request}).data, status=201)


class BrandPublicProfileView(APIView):
    """A brand's public profile (name, logo, industry, website, city).

    Creators can see it once they have an offer, order or conversation with that brand. Open
    campaigns stay anonymous (blind bidding), so a bid alone doesn't reveal the brand.
    Brands can view their own.
    """

    permission_classes = [IsBrandOrCreator]

    @extend_schema(
        responses=inline_serializer(
            "BrandPublic",
            {
                "id": serializers.UUIDField(),
                "name": serializers.CharField(),
                "logoUrl": serializers.URLField(allow_null=True),
                "industry": serializers.CharField(),
                "website": serializers.URLField(allow_null=True),
                "city": serializers.CharField(),
            },
        )
    )
    def get(self, request: Request, brand_id: UUID) -> Response:
        brand = Brand.objects.filter(pk=brand_id).first()
        if brand is None or not services.can_view_brand(request.user, brand):
            raise NotFound()
        return Response(brand_public(brand))
