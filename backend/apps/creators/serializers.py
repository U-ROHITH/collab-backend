from typing import Any

from drf_spectacular.extensions import OpenApiSerializerFieldExtension
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.serializers import MoneyField, StrictSerializer
from apps.creators.models import Creator, CreatorPackage, Platform, RepliesWithin, replies_within_label

STATS_PLATFORMS = ("instagram", "youtube")
STAT_KEYS = ("followersCount", "engagementRate", "avgViews")


def money(paise: int | None) -> dict[str, Any] | None:
    return None if paise is None else {"amount": paise, "currency": "INR"}


def normalize_handle(platform: str, value: Any) -> str:
    """Normalize a pasted platform handle or URL into a bare handle.

    Strips leading '@', query params, hash fragments, and converts profile URLs
    (e.g. 'https://www.instagram.com/democreator/') into bare handles.
    """
    if not value or not isinstance(value, str):
        return ""
    val = value.strip()
    if not val:
        return ""

    if "instagram.com" in val:
        val = val.split("instagram.com/")[-1].split("?")[0].split("#")[0].strip("/")
        if "/" in val:
            val = val.split("/")[0]
    elif "youtube.com" in val or "youtu.be" in val:
        if "youtube.com/" in val:
            val = val.split("youtube.com/")[-1].split("?")[0].split("#")[0].strip("/")
        elif "youtu.be/" in val:
            val = val.split("youtu.be/")[-1].split("?")[0].split("#")[0].strip("/")
        if val.startswith("c/"):
            val = val[2:]
        elif val.startswith("user/"):
            val = val[5:]

    return val.lstrip("@").strip()


def build_platform_url(platform: str, handle: str | None) -> str | None:
    """Construct full platform profile URL from a bare handle."""
    if not handle:
        return None
    if platform == "instagram":
        return f"https://www.instagram.com/{handle}/"
    if platform == "youtube":
        if handle.startswith("channel/") or handle.startswith("c/"):
            return f"https://www.youtube.com/{handle}"
        return f"https://www.youtube.com/@{handle}"
    return None


def _form_number(data: Any, *, percent: bool = False) -> Any:
    """Form inputs arrive as text: blank means "not set"; thousands separators (and a trailing % for rates)
    are dropped. Anything else (e.g. "30K") is left for the field to reject on that field."""
    if not isinstance(data, str):
        return data
    text = data.strip().replace(",", "").replace(" ", "").replace("\u00a0", "")
    if percent:
        text = text.removesuffix("%")
    return None if text == "" else text


class FormIntegerField(serializers.IntegerField):
    def run_validation(self, data: Any = serializers.empty) -> Any:
        return super().run_validation(_form_number(data) if data is not serializers.empty else data)


class FormRateField(serializers.FloatField):
    def run_validation(self, data: Any = serializers.empty) -> Any:
        return super().run_validation(_form_number(data, percent=True) if data is not serializers.empty else data)


class PlatformStatsInput(StrictSerializer):
    """Input for platform stats. Unknown fields are rejected with 400 by StrictSerializer."""

    handle = serializers.CharField(max_length=120, required=False, allow_blank=True)
    followersCount = FormIntegerField(min_value=0, max_value=2_000_000_000, required=False, allow_null=True)  # noqa: N815
    engagementRate = FormRateField(min_value=0, max_value=100, required=False, allow_null=True)  # noqa: N815
    avgViews = FormIntegerField(min_value=0, max_value=2_000_000_000, required=False, allow_null=True)  # noqa: N815


class PlatformStatsOutput(serializers.Serializer):
    handle = serializers.CharField(max_length=120, allow_blank=True)
    url = serializers.CharField(max_length=500, allow_blank=True)
    followersCount = serializers.IntegerField(allow_null=True)  # noqa: N815
    avgViews = serializers.IntegerField(allow_null=True)  # noqa: N815
    engagementRate = serializers.FloatField(allow_null=True)  # noqa: N815
    verified = serializers.BooleanField(default=False)


class PlatformsOutput(serializers.Serializer):
    instagram = PlatformStatsOutput(required=False, allow_null=True)
    youtube = PlatformStatsOutput(required=False, allow_null=True)


class PlatformInputField(serializers.Field):
    """Accepts either a string handle ("ananya.eats"), a dict of platform stats
    ({"handle": "ananya.eats", "followersCount": 30000, ...}), or None to remove."""

    def __init__(self, platform: str, **kwargs: Any) -> None:
        self.platform = platform
        super().__init__(**kwargs)

    def to_internal_value(self, data: Any) -> dict[str, Any] | None:
        if data is None:
            return None
        if isinstance(data, str):
            clean_handle = normalize_handle(self.platform, data)
            return {"handle": clean_handle}
        if isinstance(data, dict):
            serializer = PlatformStatsInput(data=data)
            if not serializer.is_valid():
                raise serializers.ValidationError(serializer.errors)
            val = dict(serializer.validated_data)
            if "handle" in val and val["handle"] is not None:
                val["handle"] = normalize_handle(self.platform, val["handle"])
            return val
        raise serializers.ValidationError("Must be a string handle, an object with platform stats, or null.")


class PlatformsDictField(serializers.Field):
    """Validates platforms dict where keys must be in STATS_PLATFORMS, and values
    can be a dict of stats, string handle, or None."""

    def to_internal_value(self, data: Any) -> dict[str, Any] | None:
        if data is None:
            return None
        if not isinstance(data, dict):
            raise serializers.ValidationError("Must be an object mapping platform names to stats or null.")
        unknown = sorted(set(data) - set(STATS_PLATFORMS))
        if unknown:
            raise serializers.ValidationError(f"Unknown platform(s): {', '.join(unknown)}. Use instagram or youtube.")

        result: dict[str, Any] = {}
        errors: dict[str, Any] = {}
        for platform, val in data.items():
            field = PlatformInputField(platform=platform, allow_null=True)
            try:
                result[platform] = field.run_validation(val)
            except serializers.ValidationError as exc:
                errors[platform] = exc.detail
        if errors:
            raise serializers.ValidationError(errors)
        return result


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

    def to_representation(self, value: int | None) -> str | None:
        return replies_within_label(value)

    def to_internal_value(self, data: Any) -> int:
        by_label = {label: hours for hours, label in RepliesWithin.choices}
        if data not in by_label:
            raise serializers.ValidationError(f"Use one of: {', '.join(by_label)}.")
        return by_label[data]


class PlatformInputFieldExtension(OpenApiSerializerFieldExtension):
    target_class = PlatformInputField

    def map_serializer_field(self, auto_schema: Any, direction: str) -> dict[str, Any]:
        component = auto_schema.resolve_serializer(PlatformStatsInput, direction)
        schema: dict[str, Any] = {
            "oneOf": [
                component.ref,
                {"type": "string", "description": "Bare handle, @handle, or profile URL"},
            ],
            "description": "Platform handle string, platform stats object, or null to remove.",
        }
        if getattr(self.target, "allow_null", False):
            schema["nullable"] = True
        return schema


class PlatformsDictFieldExtension(OpenApiSerializerFieldExtension):
    target_class = PlatformsDictField

    def map_serializer_field(self, auto_schema: Any, direction: str) -> dict[str, Any]:
        component = auto_schema.resolve_serializer(PlatformStatsInput, direction)
        platform_item: dict[str, Any] = {
            "oneOf": [
                component.ref,
                {"type": "string", "description": "Bare handle, @handle, or profile URL"},
            ],
            "nullable": True,
            "description": "Platform handle string, platform stats object, or null to remove.",
        }
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "instagram": platform_item,
                "youtube": platform_item,
            },
            "description": getattr(self.target, "help_text", "")
            or "{instagram|youtube: {handle, followersCount, engagementRate, avgViews} | null}. "
            "Merged per platform; null removes that platform.",
        }
        if getattr(self.target, "allow_null", False):
            schema["nullable"] = True
        return schema


class RepliesWithinFieldExtension(OpenApiSerializerFieldExtension):
    target_class = RepliesWithinField

    def map_serializer_field(self, auto_schema: Any, direction: str) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "string",
            "enum": ["1h", "6h", "24h", "3d"],
            "description": 'Response time SLA: "1h", "6h", "24h", or "3d".',
        }
        if getattr(self.target, "allow_null", False):
            schema["nullable"] = True
        return schema


class CreatorCardOutput(serializers.ModelSerializer):
    """Public view. Never includes contact details, payout info, or approval internals."""

    displayName = serializers.CharField(source="display_name")  # noqa: N815
    primaryPlatform = serializers.CharField(source="primary_platform", allow_null=True)  # noqa: N815
    repliesWithin = RepliesWithinField(source="replies_within", allow_null=True)  # noqa: N815
    followerCount = serializers.SerializerMethodField()  # noqa: N815
    avatarUrl = serializers.SerializerMethodField()  # noqa: N815
    avatar_url = serializers.SerializerMethodField()
    avatar = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()
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
            "avatar_url",
            "avatar",
            "image",
            "rating",
            "reviewCount",
            "startingPrice",
            "dob",
            "gender",
            "languages",
            "instagram",
            "youtube",
            "platforms",
            "badges",
            "featured",
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    def get_avatarUrl(self, obj: Creator) -> str | None:  # noqa: N802
        return obj.avatar_url or None

    def get_avatar_url(self, obj: Creator) -> str | None:
        return obj.avatar_url or None

    def get_avatar(self, obj: Creator) -> str | None:
        return obj.avatar_url or None

    def get_image(self, obj: Creator) -> str | None:
        return obj.avatar_url or None

    def _social(self, obj: Creator) -> dict:
        return obj.social_media_links if isinstance(obj.social_media_links, dict) else {}

    def get_city(self, obj: Creator) -> str:
        loc = obj.location or ""
        return loc.split(",")[0].strip() if loc else ""

    def get_region(self, obj: Creator) -> str:
        loc = obj.location or ""
        return loc.split(",")[1].strip() if "," in loc else ""

    def get_languages(self, obj: Creator) -> list[str]:
        return self._social(obj).get("languages", [])

    def get_instagram(self, obj: Creator) -> str:
        """Canonical bare handle for instagram, matching platforms.instagram.handle."""
        links = self._social(obj)
        val = links.get("instagram")
        if isinstance(val, dict):
            val = val.get("handle")
        return normalize_handle("instagram", val)

    def get_youtube(self, obj: Creator) -> str:
        """Canonical bare handle for youtube, matching platforms.youtube.handle."""
        links = self._social(obj)
        val = links.get("youtube")
        if isinstance(val, dict):
            val = val.get("handle")
        return normalize_handle("youtube", val)

    @extend_schema_field(PlatformsOutput)
    def get_platforms(self, obj: Creator) -> dict[str, dict[str, Any]]:
        """Platforms with a handle or any stat. Creator-entered, so `verified` is always false for now."""
        links = self._social(obj)
        all_stats = obj.platform_stats if isinstance(obj.platform_stats, dict) else {}
        out: dict[str, dict[str, Any]] = {}
        for platform in STATS_PLATFORMS:
            raw_handle = links.get(platform, "")
            if isinstance(raw_handle, dict):
                raw_handle = raw_handle.get("handle")
            handle = normalize_handle(platform, raw_handle)
            stats = all_stats.get(platform) or {}
            if not isinstance(stats, dict):
                stats = {}
            has_stats = any(stats.get(k) is not None for k in STAT_KEYS)
            if handle or has_stats:
                out[platform] = {
                    "handle": handle,
                    "url": build_platform_url(platform, handle),
                    "followersCount": stats.get("followersCount"),
                    "avgViews": stats.get("avgViews"),
                    "engagementRate": stats.get("engagementRate"),
                    "verified": False,
                }
        return out

    def get_followerCount(self, obj: Creator) -> int | None:  # noqa: N802
        """Derived from primary platform stats, fallback to other platforms, fallback to follower_count field."""
        if isinstance(obj.platform_stats, dict):
            primary = obj.primary_platform
            primary_stats = obj.platform_stats.get(primary)
            if isinstance(primary_stats, dict) and primary_stats.get("followersCount") is not None:
                return primary_stats["followersCount"]
            for p in STATS_PLATFORMS:
                stats = obj.platform_stats.get(p)
                if isinstance(stats, dict) and stats.get("followersCount") is not None:
                    return stats["followersCount"]
        return obj.follower_count

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

    @extend_schema_field(PackageOutput(many=True))
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
    avatarUrl = serializers.CharField(max_length=600, required=False, allow_blank=True, allow_null=True)  # noqa: N815
    avatar = serializers.CharField(max_length=600, required=False, allow_blank=True, allow_null=True)
    avatarUploadId = serializers.UUIDField(required=False, allow_null=True)  # noqa: N815
    followerCount = FormIntegerField(min_value=0, max_value=2_000_000_000, required=False, allow_null=True)  # noqa: N815
    instagram = PlatformInputField(platform="instagram", required=False, allow_null=True)
    youtube = PlatformInputField(platform="youtube", required=False, allow_null=True)
    platforms = PlatformsDictField(
        required=False,
        allow_null=True,
        help_text="{instagram|youtube: {handle, followersCount, engagementRate, avgViews} | null}. "
        "Merged per platform; null removes that platform.",
    )


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
