from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.uploads.models import Upload, UploadKind


class UploadCreateInput(StrictSerializer):
    kind = serializers.ChoiceField(choices=UploadKind.choices)
    contentType = serializers.CharField(max_length=60)  # noqa: N815
    size = serializers.IntegerField(min_value=1, help_text="File size in bytes")


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
