from typing import Any

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.uploads.models import Upload, UploadKind


class UploadCreateInput(StrictSerializer):
    kind = serializers.ChoiceField(choices=UploadKind.choices)
    contentType = serializers.CharField(max_length=60, required=False)  # noqa: N815
    mimeType = serializers.CharField(max_length=60, required=False)  # noqa: N815
    size = serializers.IntegerField(min_value=1, required=False, help_text="File size in bytes")
    sizeBytes = serializers.IntegerField(min_value=1, required=False)  # noqa: N815
    fileSize = serializers.IntegerField(min_value=1, required=False)  # noqa: N815
    filename = serializers.CharField(max_length=255, required=False, allow_blank=True)
    fileName = serializers.CharField(max_length=255, required=False, allow_blank=True)  # noqa: N815
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        attrs = dict(attrs)
        content_type = attrs.get("contentType") or attrs.get("mimeType")
        if not content_type:
            raise serializers.ValidationError({"contentType": ["This field is required."]})
        attrs["contentType"] = content_type

        size = attrs.get("size") or attrs.get("sizeBytes") or attrs.get("fileSize")
        if not size:
            raise serializers.ValidationError({"size": ["This field is required."]})
        attrs["size"] = size
        return attrs


class UploadOutput(serializers.ModelSerializer):
    contentType = serializers.SerializerMethodField()  # noqa: N815
    size = serializers.SerializerMethodField()
    url = serializers.SerializerMethodField()

    class Meta:
        model = Upload
        fields = ("id", "kind", "status", "contentType", "size", "url")

    def get_contentType(self, obj: Upload) -> str:  # noqa: N802
        return obj.content_type or obj.declared_content_type

    def get_size(self, obj: Upload) -> int:
        return obj.size_bytes or obj.declared_size

    def get_url(self, obj: Upload) -> str | None:
        return obj.public_url or None  # private files: use the download-url endpoint


class UploadCreatedOutput(serializers.Serializer):
    upload = UploadOutput()
    uploadUrl = serializers.URLField()  # noqa: N815
    method = serializers.CharField()
    headers = serializers.DictField(child=serializers.CharField())


class DownloadUrlOutput(serializers.Serializer):
    url = serializers.URLField()
    expiresIn = serializers.IntegerField(allow_null=True)  # noqa: N815
