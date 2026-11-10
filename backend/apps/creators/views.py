import os
import secrets
from uuid import UUID

from django.core.files.storage import default_storage
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core import cache as cache_utils
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
    UploadInput,
    UploadOutput,
)
from apps.identity.permissions import IsCreator


def _my_creator(request: Request) -> Creator:
    creator = getattr(request.user, "creator", None)
    if creator is None:
        raise NotFound("Creator profile not found.")
    return creator


class CreatorMeView(APIView):
    permission_classes = [IsCreator]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_scope = "writes"

    @extend_schema(responses=CreatorSelfOutput)
    def get(self, request: Request) -> Response:
        return Response(CreatorSelfOutput(_my_creator(request), context={"request": request}).data)

    @extend_schema(request=CreatorProfileInput, responses=CreatorSelfOutput)
    def patch(self, request: Request) -> Response:
        data = CreatorProfileInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        creator = services.update_creator_profile(creator=_my_creator(request), data=data.validated_data)
        cache_utils.invalidate_creator_cache(creator.pk)
        return Response(CreatorSelfOutput(creator, context={"request": request}).data)

    @extend_schema(request=CreatorProfileInput, responses=CreatorSelfOutput)
    def put(self, request: Request) -> Response:
        return self.patch(request)

    @extend_schema(request=CreatorProfileInput, responses=CreatorSelfOutput)
    def post(self, request: Request) -> Response:
        return self.patch(request)


class UploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    throttle_scope = "writes"

    @extend_schema(request=UploadInput, responses={201: UploadOutput})
    def post(self, request: Request) -> Response:
        data = UploadInput(data=request.data)
        data.is_valid(raise_exception=True)
        file = data.validated_data.get("file")
        if not file:
            raise serializers.ValidationError({"file": ["No file was submitted."]})

        kind = data.validated_data.get("kind", "avatar")
        ext = os.path.splitext(file.name)[1].lower() or ".png"
        folder = "avatars" if kind == "avatar" else "uploads"
        filename = f"creators/{folder}/{secrets.token_hex(16)}{ext}"
        saved_path = default_storage.save(filename, file)
        relative_url = default_storage.url(saved_path)
        public_url = request.build_absolute_uri(relative_url)

        # Profile Persistence: Bind to authenticated creator's profile
        creator = getattr(request.user, "creator", None)
        if creator and kind in ("avatar", "profile_picture", "photo"):
            creator.avatar_url = public_url
            creator.profile_picture = saved_path
            creator.save(update_fields=["avatar_url", "profile_picture", "updated_at"])
            cache_utils.invalidate_creator_cache(creator.pk)

        return Response(
            {
                "url": public_url,
                "avatarUrl": public_url,
                "kind": kind,
                "filename": file.name,
                "size": file.size,
                "contentType": getattr(file, "content_type", "image/png"),
            },
            status=status.HTTP_201_CREATED,
        )


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
        cache_utils.invalidate_creator_cache(package.creator_id)
        return Response(PackageOutput(package).data, status=status.HTTP_201_CREATED)


class MyPackageDetailView(APIView):
    permission_classes = [IsCreator]
    throttle_scope = "writes"

    @extend_schema(request=PackageInput, responses=PackageOutput)
    def patch(self, request: Request, package_id: UUID) -> Response:
        data = PackageInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        package = services.update_package(creator=_my_creator(request), package_id=package_id, data=data.validated_data)
        cache_utils.invalidate_creator_cache(package.creator_id)
        return Response(PackageOutput(package).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, package_id: UUID) -> Response:
        creator = _my_creator(request)
        services.deactivate_package(creator=creator, package_id=package_id)
        cache_utils.invalidate_creator_cache(creator.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CreatorSearchView(ListAPIView):
    """Discovery: approved creators only. Visible publicly and anonymously."""

    permission_classes = [AllowAny]
    serializer_class = CreatorCardOutput
    throttle_scope = "anon_discovery"

    @extend_schema(parameters=[CreatorSearchParams])
    def get(self, request: Request, *args: object, **kwargs: object) -> Response:
        return super().get(request, *args, **kwargs)

    def get_queryset(self):  # type: ignore[no-untyped-def]
        params = CreatorSearchParams(data=self.request.query_params.dict())
        params.is_valid(raise_exception=True)
        return selectors.search_creators(params.validated_data)


class CreatorDetailView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "anon_discovery"

    @extend_schema(responses=CreatorDetailOutput)
    def get(self, request: Request, creator_id: UUID) -> Response:
        cached = cache_utils.get_cached_creator_detail(creator_id)
        if cached is not None:
            resp = Response(cached)
            resp["X-Cache"] = "HIT"
            return resp

        creator = selectors.get_public_creator(creator_id)
        if creator is None:
            raise NotFound()
        data = CreatorDetailOutput(creator, context={"request": request}).data
        cache_utils.set_cached_creator_detail(creator_id, data)
        resp = Response(data)
        resp["X-Cache"] = "MISS"
        return resp
