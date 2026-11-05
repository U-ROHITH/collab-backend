import json
from collections.abc import Mapping
from typing import Any

from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from apps.core.serializers import MoneyField, StrictSerializer
from apps.creators.models import Creator, CreatorPackage, Platform, RepliesWithin

MAX_PROFILE_PICTURE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


def money(paise: int | None) -> dict[str, Any] | None:
    return None if paise is None else {"amount": paise, "currency": "INR"}


class PackageOutput(serializers.ModelSerializer):
    price = serializers.SerializerMethodField()
    deliveryDays = serializers.IntegerField(source="delivery_days")  # noqa: N815

    class Meta:
        model = CreatorPackage
        fields = ("id", "title", "description", "platform", "deliverables", "price", "deliveryDays", "active")

    def get_price(self, obj: CreatorPackage) -> dict[str, Any] | None:
        return money(obj.price_paise)


class PackageInput(StrictSerializer):
    title = serializers.CharField(max_length=120)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    platform = serializers.ChoiceField(choices=Platform.choices)
    deliverables = serializers.CharField(max_length=200)
    price = MoneyField(help_text="Integer paise")
    deliveryDays = serializers.IntegerField(min_value=1, max_value=90)  # noqa: N815
    active = serializers.BooleanField(required=False)


class CreatorCardOutput(serializers.ModelSerializer):
    """Public view. Never includes contact details, payout info, or approval internals."""

    displayName = serializers.CharField(source="display_name")  # noqa: N815
    primaryPlatform = serializers.CharField(source="primary_platform")  # noqa: N815
    repliesWithin = serializers.CharField(source="replies_within")  # noqa: N815
    followerCount = serializers.IntegerField(source="follower_count")  # noqa: N815
    avatarUrl = serializers.SerializerMethodField()  # noqa: N815
    profilePicture = serializers.SerializerMethodField()  # noqa: N815
    socialMediaUrls = serializers.JSONField(source="social_media_links", read_only=True)  # noqa: N815
    rating = serializers.FloatField()
    reviewCount = serializers.IntegerField(source="rating_count")  # noqa: N815
    startingPrice = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = Creator
        fields = (
            "id",
            "slug",
            "displayName",
            "bio",
            "categories",
            "location",
            "primaryPlatform",
            "repliesWithin",
            "verified",
            "followerCount",
            "avatarUrl",
            "profilePicture",
            "socialMediaUrls",
            "rating",
            "reviewCount",
            "startingPrice",
        )

    def get_avatarUrl(self, obj: Creator) -> str | None:  # noqa: N802
        url = obj.avatar_url
        if not url and bool(obj.profile_picture):
            url = obj.profile_picture.url
        if url:
            request = self.context.get("request")
            if request and not url.startswith("http://") and not url.startswith("https://"):
                return request.build_absolute_uri(url)
            return url
        return None

    def get_profilePicture(self, obj: Creator) -> str | None:  # noqa: N802
        if bool(obj.profile_picture):
            url = obj.profile_picture.url
            request = self.context.get("request")
            if request and not url.startswith("http://") and not url.startswith("https://"):
                return request.build_absolute_uri(url)
            return url
        return self.get_avatarUrl(obj)

    def get_startingPrice(self, obj: Creator) -> dict[str, Any] | None:  # noqa: N802
        return money(getattr(obj, "starting_price", None))


class CreatorDetailOutput(CreatorCardOutput):
    packages = serializers.SerializerMethodField()

    class Meta(CreatorCardOutput.Meta):
        fields = (*CreatorCardOutput.Meta.fields, "packages")

    def get_packages(self, obj: Creator) -> list[dict[str, Any]]:
        packages = getattr(obj, "active_packages", None)
        if packages is None:
            packages = obj.packages.filter(active=True)
        return PackageOutput(packages, many=True).data


class CreatorSelfOutput(CreatorCardOutput):
    approvalStatus = serializers.CharField(source="approval_status")  # noqa: N815
    packages = PackageOutput(many=True, read_only=True)

    class Meta(CreatorCardOutput.Meta):
        fields = (*CreatorCardOutput.Meta.fields, "approvalStatus", "packages")


class ProfilePictureField(serializers.ImageField):
    def to_internal_value(self, data: Any) -> Any:
        if data and hasattr(data, "size") and data.size > MAX_PROFILE_PICTURE_SIZE_BYTES:
            raise serializers.ValidationError("Profile picture file size cannot exceed 5MB.")
        return super().to_internal_value(data)


class UploadInput(StrictSerializer):
    file = serializers.FileField(required=False)
    avatar = serializers.FileField(required=False)
    profilePicture = serializers.FileField(required=False)  # noqa: N815
    kind = serializers.CharField(max_length=40, required=False, default="avatar")

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            plain = {k: data[k] for k in data}
            data = plain
            if "file" not in data:
                if "avatar" in data:
                    data["file"] = data["avatar"]
                elif "profilePicture" in data:
                    data["file"] = data["profilePicture"]
        return super().to_internal_value(data)

    def validate_file(self, value: Any) -> Any:
        if not value:
            raise serializers.ValidationError("A file must be provided.")
        if hasattr(value, "size") and value.size > MAX_PROFILE_PICTURE_SIZE_BYTES:
            raise serializers.ValidationError("File size cannot exceed 5MB.")
        try:
            img = Image.open(value)
            img.verify()
            fmt = (img.format or "").upper()
            if fmt not in ("PNG", "JPEG", "JPG", "WEBP", "GIF"):
                raise serializers.ValidationError(
                    "Upload a valid image. Supported formats are PNG, JPEG, WEBP, and GIF."
                )
            value.seek(0)
        except (UnidentifiedImageError, OSError, SyntaxError):
            raise serializers.ValidationError(
                "Upload a valid image. The file you uploaded was either not an image or a corrupted image."
            ) from None
        return value


class UploadOutput(serializers.Serializer):
    url = serializers.CharField()
    avatarUrl = serializers.CharField()  # noqa: N815
    kind = serializers.CharField()
    filename = serializers.CharField()
    size = serializers.IntegerField()
    contentType = serializers.CharField(required=False)  # noqa: N815


class CreatorProfileInput(StrictSerializer):
    displayName = serializers.CharField(max_length=80, required=False)  # noqa: N815
    bio = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    categories = serializers.ListField(child=serializers.CharField(max_length=40), max_length=5, required=False)
    location = serializers.CharField(max_length=80, required=False, allow_blank=True)
    primaryPlatform = serializers.ChoiceField(choices=Platform.choices, required=False)  # noqa: N815
    repliesWithin = serializers.ChoiceField(choices=RepliesWithin.choices, required=False)  # noqa: N815
    socialMediaUrls = serializers.JSONField(required=False)  # noqa: N815
    profilePicture = ProfilePictureField(required=False, allow_null=True)  # noqa: N815
    avatar = ProfilePictureField(required=False, allow_null=True)
    avatarUrl = serializers.URLField(required=False, allow_blank=True)  # noqa: N815

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            # Create a plain dict shallow copy without deepcopying file handles
            plain = {}
            for k in data:
                if hasattr(data, "getlist") and k == "categories":
                    vals = data.getlist(k)
                    plain[k] = vals if len(vals) > 1 else data[k]
                else:
                    plain[k] = data[k]
            data = plain

        if isinstance(data, dict):
            # Normalize 'name' alias to canonical 'displayName'
            if "name" in data:
                if "displayName" not in data:
                    data["displayName"] = data["name"]
                del data["name"]

            # Normalize 'profile_picture' and 'avatar' aliases to canonical 'profilePicture'
            if "profile_picture" in data:
                if "profilePicture" not in data:
                    data["profilePicture"] = data["profile_picture"]
                del data["profile_picture"]

            if "avatar" in data:
                if "profilePicture" not in data:
                    data["profilePicture"] = data["avatar"]
                del data["avatar"]

            if "avatar_url" in data:
                if "avatarUrl" not in data:
                    data["avatarUrl"] = data["avatar_url"]
                del data["avatar_url"]

            # Normalize social links aliases to canonical 'socialMediaUrls'
            for alias in ("socialLinks", "social_media_urls", "social_links", "socialMediaLinks"):
                if alias in data:
                    if "socialMediaUrls" not in data:
                        data["socialMediaUrls"] = data[alias]
                    del data[alias]

            if "categories" in data and isinstance(data["categories"], str):
                try:
                    parsed = json.loads(data["categories"])
                    if isinstance(parsed, list):
                        data["categories"] = parsed
                except (json.JSONDecodeError, TypeError):
                    if "," in data["categories"]:
                        data["categories"] = [c.strip() for c in data["categories"].split(",") if c.strip()]
                    elif data["categories"].strip():
                        data["categories"] = [data["categories"].strip()]

        return super().to_internal_value(data)

    def validate_profilePicture(self, value: Any) -> Any:  # noqa: N802
        if value and hasattr(value, "size") and value.size > MAX_PROFILE_PICTURE_SIZE_BYTES:
            raise serializers.ValidationError("Profile picture file size cannot exceed 5MB.")
        return value

    def validate_socialMediaUrls(self, value: Any) -> Any:  # noqa: N802
        return self._validate_social_payload(value)

    def _validate_social_payload(self, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                if value.startswith("http://") or value.startswith("https://"):
                    value = {"url": value}
                else:
                    raise serializers.ValidationError(
                        "Enter valid social media URLs or a JSON object of links."
                    ) from None
        if not isinstance(value, (dict, list)):
            raise serializers.ValidationError("Social media URLs must be a dictionary or list of links.")
        return value


class CreatorSearchParams(StrictSerializer):
    q = serializers.CharField(max_length=100, required=False)
    category = serializers.CharField(max_length=40, required=False)
    location = serializers.CharField(max_length=80, required=False)
    platform = serializers.ChoiceField(choices=Platform.choices, required=False)
    verified = serializers.BooleanField(required=False)
    minPrice = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    maxPrice = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    minFollowers = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    # pagination params are allowed through
    limit = serializers.IntegerField(min_value=1, max_value=50, required=False)
    cursor = serializers.CharField(max_length=500, required=False)
