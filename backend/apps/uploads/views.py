from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.identity.permissions import IsBrandOrCreator, IsCreator
from apps.uploads import services
from apps.uploads.serializers import DownloadUrlOutput, UploadCreatedOutput, UploadCreateInput, UploadOutput


class UploadCreateView(APIView):
    """Step 1: get a single-use URL. Step 2: PUT the file there. Step 3: call /complete/."""

    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=UploadCreateInput, responses={201: UploadCreatedOutput})
    def post(self, request: Request) -> Response:
        data = UploadCreateInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        upload, url = services.create_upload(
            user=request.user, kind=v["kind"], content_type=v["contentType"], size=v["size"]
        )
        return Response(
            {
                "upload": UploadOutput(upload).data,
                "uploadUrl": url,
                "method": "PUT",
                "headers": {"Content-Type": upload.declared_content_type},
            },
            status=status.HTTP_201_CREATED,
        )


class UploadCompleteView(APIView):
    permission_classes = [IsBrandOrCreator]
    throttle_scope = "writes"

    @extend_schema(request=None, responses=UploadOutput)
    def post(self, request: Request, upload_id: UUID) -> Response:
        return Response(UploadOutput(services.complete_upload(user=request.user, upload_id=upload_id)).data)


class UploadDownloadUrlView(APIView):
    permission_classes = [IsBrandOrCreator]

    @extend_schema(responses=DownloadUrlOutput)
    def get(self, request: Request, upload_id: UUID) -> Response:
        return Response(services.download_url(user=request.user, upload_id=upload_id))


class GalleryItemView(APIView):
    permission_classes = [IsCreator]

    @extend_schema(responses={204: None})
    def delete(self, request: Request, media_id: UUID) -> Response:
        services.delete_gallery_item(user=request.user, media_id=media_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
