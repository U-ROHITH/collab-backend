from typing import Any

from rest_framework import serializers

from apps.core.serializers import MoneyField, StrictSerializer
from apps.creators.models import Creator, CreatorPackage, Platform, RepliesWithin


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
    avatarUrl = serializers.CharField(source="avatar_url")  # noqa: N815
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
            "rating",
            "reviewCount",
            "startingPrice",
        )

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


class CreatorProfileInput(StrictSerializer):
    displayName = serializers.CharField(max_length=80, required=False)  # noqa: N815
    bio = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    categories = serializers.ListField(child=serializers.CharField(max_length=40), max_length=5, required=False)
    location = serializers.CharField(max_length=80, required=False, allow_blank=True)
    primaryPlatform = serializers.ChoiceField(choices=Platform.choices, required=False)  # noqa: N815
    repliesWithin = serializers.ChoiceField(choices=RepliesWithin.choices, required=False)  # noqa: N815


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
