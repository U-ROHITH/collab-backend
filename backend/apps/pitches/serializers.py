from typing import Any

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.pitches.models import Pitch


class PitchOutput(serializers.ModelSerializer):
    creatorId = serializers.UUIDField(source="creator_id")  # noqa: N815
    brandId = serializers.UUIDField(source="brand_id")  # noqa: N815
    campaignId = serializers.UUIDField(source="campaign_id", allow_null=True)  # noqa: N815
    editCount = serializers.IntegerField(source="edit_count")  # noqa: N815
    editedAt = serializers.DateTimeField(source="edited_at", allow_null=True)  # noqa: N815
    readAt = serializers.DateTimeField(source="read_at", allow_null=True)  # noqa: N815
    declinedAt = serializers.DateTimeField(source="declined_at", allow_null=True)  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Pitch
        fields = (
            "id",
            "creatorId",
            "brandId",
            "campaignId",
            "message",
            "links",
            "status",
            "editCount",
            "editedAt",
            "readAt",
            "declinedAt",
            "createdAt",
        )


def _validate_links(value: list[str]) -> list[str]:
    if len(value) > 5:
        raise serializers.ValidationError("At most 5 links are allowed.")
    for url in value:
        if len(url) > 300:
            raise serializers.ValidationError("Each link must be at most 300 characters.")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise serializers.ValidationError("Each link must start with http:// or https://.")
    return value


def _validate_message(value: str) -> str:
    stripped = value.strip()
    if len(stripped) < 20 or len(stripped) > 1500:
        raise serializers.ValidationError("Message must be between 20 and 1500 characters.")
    return stripped


class PitchCreateInput(StrictSerializer):
    message = serializers.CharField(max_length=1500)
    links = serializers.ListField(
        child=serializers.URLField(max_length=300),
        required=False,
        default=list,
        max_length=5,
    )
    campaignId = serializers.UUIDField(required=False, allow_null=True)  # noqa: N815

    def validate_message(self, value: str) -> str:
        return _validate_message(value)

    def validate_links(self, value: list[str]) -> list[str]:
        return _validate_links(value)


class PitchEditInput(StrictSerializer):
    message = serializers.CharField(max_length=1500, required=False)
    links = serializers.ListField(
        child=serializers.URLField(max_length=300),
        required=False,
        max_length=5,
    )
    campaignId = serializers.UUIDField(required=False, allow_null=True)  # noqa: N815

    def validate_message(self, value: str) -> str:
        return _validate_message(value)

    def validate_links(self, value: list[str]) -> list[str]:
        return _validate_links(value)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs:
            raise serializers.ValidationError("At least one field must be provided to edit.")
        return attrs


class BrandPitchListParams(StrictSerializer):
    campaignId = serializers.UUIDField(required=False)  # noqa: N815
    status = serializers.ChoiceField(choices=["sent", "edited", "declined"], required=False)
    unread = serializers.IntegerField(min_value=0, max_value=1, required=False)
