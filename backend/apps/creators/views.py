from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import NotFound
from apps.creators import selectors, services
from apps.creators.models import Creator
from apps.creators.serializers import (
    CreatorCardOutput,
    CreatorDetailOutput,
    CreatorProfileInput,
    CreatorSearchParams,
    CreatorSelfOutput,
    PackageInput,
    PackageOutput,
)
from apps.identity.permissions import IsCreator


def _my_creator(request: Request) -> Creator:
    creator = getattr(request.user, "creator", None)
    if creator is None:
        raise NotFound("Creator profile not found.")
    return creator


class CreatorMeView(APIView):
    permission_classes = [IsCreator]
    throttle_scope = "writes"

    @extend_schema(responses=CreatorSelfOutput)
    def get(self, request: Request) -> Response:
        return Response(CreatorSelfOutput(_my_creator(request)).data)

    @extend_schema(request=CreatorProfileInput, responses=CreatorSelfOutput)
    def patch(self, request: Request) -> Response:
        data = CreatorProfileInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        creator = services.update_creator_profile(creator=_my_creator(request), data=data.validated_data)
        return Response(CreatorSelfOutput(creator).data)


class MyPackagesView(APIView):
    permission_classes = [IsCreator]
    throttle_scope = "writes"

    @extend_schema(responses=PackageOutput(many=True))
    def get(self, request: Request) -> Response:
        return Response(PackageOutput(_my_creator(request).packages.all(), many=True).data)

    @extend_schema(request=PackageInput, responses={201: PackageOutput})
    def post(self, request: Request) -> Response:
        data = PackageInput(data=request.data)
        data.is_valid(raise_exception=True)
        package = services.create_package(creator=_my_creator(request), data=data.validated_data)
        return Response(PackageOutput(package).data, status=status.HTTP_201_CREATED)


class MyPackageDetailView(APIView):
    permission_classes = [IsCreator]
    throttle_scope = "writes"

    @extend_schema(request=PackageInput, responses=PackageOutput)
    def patch(self, request: Request, package_id: UUID) -> Response:
        data = PackageInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        package = services.update_package(creator=_my_creator(request), package_id=package_id, data=data.validated_data)
        return Response(PackageOutput(package).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, package_id: UUID) -> Response:
        services.deactivate_package(creator=_my_creator(request), package_id=package_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CreatorSearchView(ListAPIView):
    """Discovery: approved creators only. Public (guest view); the card never includes contact or payout info."""

    permission_classes = [AllowAny]
    serializer_class = CreatorCardOutput

    @extend_schema(parameters=[CreatorSearchParams])
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self):  # type: ignore[no-untyped-def]
        params = CreatorSearchParams(data=self.request.query_params.dict())
        params.is_valid(raise_exception=True)
        return selectors.search_creators(params.validated_data)


class CreatorDetailView(APIView):
    """Public profile of an approved creator (guest view)."""

    permission_classes = [AllowAny]

    @extend_schema(responses=CreatorDetailOutput)
    def get(self, request: Request, creator_id: UUID) -> Response:
        creator = selectors.get_public_creator(creator_id)
        if creator is None:
            raise NotFound()
        return Response(CreatorDetailOutput(creator).data)
