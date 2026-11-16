from typing import Any

from rest_framework import serializers

from apps.core.serializers import MoneyField, StrictSerializer
from apps.creators.models import Creator, CreatorPackage, Platform, RepliesWithin, replies_within_label


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


class RepliesWithinField(serializers.Field):
    """Hours in the database, "1h" | "6h" | "24h" | "3d" on the wire."""

    def to_representation(self, value: int) -> str:
        return replies_within_label(value)

    def to_internal_value(self, data: Any) -> int:
        by_label = {label: hours for hours, label in RepliesWithin.choices}
        if data not in by_label:
            raise serializers.ValidationError(f"Use one of: {', '.join(by_label)}.")
        return by_label[data]


class CreatorCardOutput(serializers.ModelSerializer):
    """Public view. Never includes contact details, payout info, or approval internals."""

    displayName = serializers.CharField(source="display_name")  # noqa: N815
    primaryPlatform = serializers.CharField(source="primary_platform")  # noqa: N815
    repliesWithin = RepliesWithinField(source="replies_within")  # noqa: N815
    followerCount = serializers.IntegerField(source="follower_count")  # noqa: N815
    avatarUrl = serializers.CharField(source="avatar_url")  # noqa: N815
    rating = serializers.FloatField()
    reviewCount = serializers.IntegerField(source="rating_count")  # noqa: N815
    startingPrice = serializers.SerializerMethodField()  # noqa: N815
    city = serializers.SerializerMethodField()
    region = serializers.SerializerMethodField()
    languages = serializers.SerializerMethodField()
    instagram = serializers.SerializerMethodField()
    youtube = serializers.SerializerMethodField()
    platforms = serializers.SerializerMethodField()

    class Meta:
        model = Creator
        fields = (
            "id",
            "slug",
            "displayName",
            "bio",
            "categories",
            "location",
            "city",
            "region",
            "primaryPlatform",
            "repliesWithin",
            "verified",
            "followerCount",
            "avatarUrl",
            "rating",
            "reviewCount",
            "startingPrice",
            "dob",
            "gender",
            "languages",
            "instagram",
            "youtube",
            "platforms",
        )

    def get_city(self, obj: Creator) -> str:
        loc = obj.location or ""
        return loc.split(",")[0].strip() if loc else ""

    def get_region(self, obj: Creator) -> str:
        loc = obj.location or ""
        return loc.split(",")[1].strip() if "," in loc else ""

    def get_languages(self, obj: Creator) -> list[str]:
        if isinstance(obj.social_media_links, dict):
            return obj.social_media_links.get("languages", [])
        return []

    def get_instagram(self, obj: Creator) -> str:
        if isinstance(obj.social_media_links, dict):
            return obj.social_media_links.get("instagram", "")
        return ""

    def get_youtube(self, obj: Creator) -> str:
        if isinstance(obj.social_media_links, dict):
            return obj.social_media_links.get("youtube", "")
        return ""

    def get_platforms(self, obj: Creator) -> dict[str, dict[str, Any]]:
        """Platforms with a handle or any stat. Creator-entered, so `verified` is always false for now."""
        links = obj.social_media_links if isinstance(obj.social_media_links, dict) else {}
        all_stats = obj.platform_stats if isinstance(obj.platform_stats, dict) else {}
        out: dict[str, dict[str, Any]] = {}
        for platform in STATS_PLATFORMS:
            stats = all_stats.get(platform) or {}
            entry = {"handle": links.get(platform, "") or ""} | {k: stats.get(k) for k in STAT_KEYS}
            if entry["handle"] or any(entry[k] is not None for k in STAT_KEYS):
                out[platform] = entry | {"verified": False}
        return out

    def get_startingPrice(self, obj: Creator) -> dict[str, Any] | None:  # noqa: N802
        return money(getattr(obj, "starting_price", None))


class GalleryItemOutput(serializers.Serializer):
    id = serializers.UUIDField()
    url = serializers.URLField()


class CreatorDetailOutput(CreatorCardOutput):
    packages = serializers.SerializerMethodField()
    gallery = GalleryItemOutput(source="media", many=True, read_only=True)

    class Meta(CreatorCardOutput.Meta):
        fields = (*CreatorCardOutput.Meta.fields, "packages", "gallery")

    def get_packages(self, obj: Creator) -> list[dict[str, Any]]:
        packages = getattr(obj, "active_packages", None)
        if packages is None:
            packages = obj.packages.filter(active=True)
        return PackageOutput(packages, many=True).data


class CreatorSelfOutput(CreatorCardOutput):
    approvalStatus = serializers.CharField(source="approval_status")  # noqa: N815
    packages = PackageOutput(many=True, read_only=True)
    gallery = GalleryItemOutput(source="media", many=True, read_only=True)

    class Meta(CreatorCardOutput.Meta):
        fields = (*CreatorCardOutput.Meta.fields, "approvalStatus", "packages", "gallery")


STATS_PLATFORMS = ("instagram", "youtube")
STAT_KEYS = ("followersCount", "engagementRate", "avgViews")


class PlatformStatsInput(StrictSerializer):
    handle = serializers.CharField(max_length=120, required=False, allow_blank=True)
    followersCount = serializers.IntegerField(min_value=0, max_value=2_000_000_000, required=False, allow_null=True)  # noqa: N815
    engagementRate = serializers.FloatField(min_value=0, max_value=100, required=False, allow_null=True)  # noqa: N815
    avgViews = serializers.IntegerField(min_value=0, max_value=2_000_000_000, required=False, allow_null=True)  # noqa: N815

    def validate_handle(self, value: str) -> str:
        return value.strip().lstrip("@")


class CreatorProfileInput(StrictSerializer):
    displayName = serializers.CharField(max_length=80, required=False)  # noqa: N815
    bio = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    categories = serializers.ListField(child=serializers.CharField(max_length=40), max_length=10, required=False)
    languages = serializers.ListField(child=serializers.CharField(max_length=40), required=False)
    location = serializers.CharField(max_length=120, required=False, allow_blank=True)
    city = serializers.CharField(max_length=80, required=False, allow_blank=True)
    region = serializers.CharField(max_length=80, required=False, allow_blank=True)
    primaryPlatform = serializers.ChoiceField(choices=Platform.choices, required=False)  # noqa: N815
    repliesWithin = RepliesWithinField(required=False)  # noqa: N815
    dob = serializers.DateField(required=False, allow_null=True)
    gender = serializers.CharField(max_length=40, required=False, allow_blank=True)
    instagram = serializers.CharField(max_length=120, required=False, allow_blank=True)
    youtube = serializers.CharField(max_length=120, required=False, allow_blank=True)
    platforms = serializers.DictField(
        child=PlatformStatsInput(allow_null=True),
        required=False,
        help_text="{instagram|youtube: {handle, followersCount, engagementRate, avgViews} | null}. "
        "Merged per platform; null removes that platform.",
    )

    def validate_platforms(self, value: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(value) - set(STATS_PLATFORMS))
        if unknown:
            raise serializers.ValidationError(f"Unknown platform(s): {', '.join(unknown)}. Use instagram or youtube.")
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
