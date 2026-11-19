from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import EnvelopeCursorPagination
from apps.identity.permissions import IsApprovedCreator, IsBrand, IsCreator
from apps.pitches import services
from apps.pitches.models import Pitch
from apps.pitches.selectors import pitches_for_brand, pitches_for_creator
from apps.pitches.serializers import (
    BrandPitchListParams,
    PitchCreateInput,
    PitchEditInput,
    PitchOutput,
)


class BrandPitchCreateView(APIView):
    """POST /v1/brands/{brand_id}/pitches/ — creator pitches a brand."""

    permission_classes = [IsApprovedCreator]
    throttle_scope = "writes"

    @extend_schema(request=PitchCreateInput, responses={201: PitchOutput})
    def post(self, request: Request, brand_id: UUID) -> Response:
        data = PitchCreateInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        pitch = services.create_pitch(
            actor_user=request.user,
            brand_id=brand_id,
            message=v["message"],
            links=v.get("links", []),
            campaign_id=v.get("campaignId"),
        )
        return Response(PitchOutput(pitch).data, status=status.HTTP_201_CREATED)


class PitchEditView(APIView):
    """PATCH /v1/pitches/{id}/ — creator edits their sent pitch (once)."""

    permission_classes = [IsCreator]
    throttle_scope = "writes"

    @extend_schema(request=PitchEditInput, responses=PitchOutput)
    def patch(self, request: Request, pitch_id: UUID) -> Response:
        data = PitchEditInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        campaign_id_provided = "campaignId" in request.data
        pitch = services.edit_pitch(
            actor_user=request.user,
            pitch_id=pitch_id,
            message=v.get("message"),
            links=v.get("links"),
            campaign_id=v.get("campaignId"),
            campaign_id_provided=campaign_id_provided,
        )
        return Response(PitchOutput(pitch).data)


class PitchReadView(APIView):
    """POST /v1/pitches/{id}/read/ — brand marks pitch as read."""

    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=None, responses=PitchOutput)
    def post(self, request: Request, pitch_id: UUID) -> Response:
        pitch = services.mark_read(actor_user=request.user, pitch_id=pitch_id)
        return Response(PitchOutput(pitch).data)


class PitchDeclineView(APIView):
    """POST /v1/pitches/{id}/decline/ — brand declines pitch."""

    permission_classes = [IsBrand]
    throttle_scope = "writes"

    @extend_schema(request=None, responses=PitchOutput)
    def post(self, request: Request, pitch_id: UUID) -> Response:
        pitch = services.decline_pitch(actor_user=request.user, pitch_id=pitch_id)
        return Response(PitchOutput(pitch).data)


class CreatorPitchListView(ListAPIView):
    """GET /v1/creators/me/pitches/ — creator's outgoing pitches."""

    permission_classes = [IsCreator]
    serializer_class = PitchOutput
    pagination_class = EnvelopeCursorPagination

    def get_queryset(self):  # type: ignore[no-untyped-def]
        creator = getattr(self.request.user, "creator", None)
        if creator is None:
            return Pitch.objects.none()
        return pitches_for_creator(creator.id)


class BrandPitchListView(ListAPIView):
    """GET /v1/brands/me/pitches/ — brand's incoming pitches."""

    permission_classes = [IsBrand]
    serializer_class = PitchOutput
    pagination_class = EnvelopeCursorPagination

    def get_queryset(self):  # type: ignore[no-untyped-def]
        brand = getattr(self.request.user, "brand", None)
        if brand is None:
            return Pitch.objects.none()
        params_ser = BrandPitchListParams(data=self.request.query_params.dict())
        params_ser.is_valid(raise_exception=True)
        return pitches_for_brand(brand.id, params_ser.validated_data)
