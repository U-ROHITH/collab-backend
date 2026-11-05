from typing import Any

from rest_framework import serializers

from apps.campaigns.models import Bid, Campaign, CampaignStatus
from apps.core.serializers import MoneyField, StrictSerializer, UsageRightsSerializer
from apps.creators.models import Platform
from apps.creators.serializers import money


class CampaignOutput(serializers.ModelSerializer):
    brand = serializers.SerializerMethodField()
    budgetMin = serializers.SerializerMethodField()  # noqa: N815
    budgetMax = serializers.SerializerMethodField()  # noqa: N815
    closesAt = serializers.DateTimeField(source="closes_at")  # noqa: N815
    usageRights = serializers.JSONField(source="usage_rights")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Campaign
        fields = (
            "id",
            "title",
            "brief",
            "platform",
            "deliverables",
            "budgetMin",
            "budgetMax",
            "closesAt",
            "status",
            "usageRights",
            "brand",
            "createdAt",
        )

    def get_brand(self, obj: Campaign) -> dict[str, Any]:
        return {"id": str(obj.brand_id), "name": obj.brand.name, "logoUrl": obj.brand.logo_url}

    def get_budgetMin(self, obj: Campaign) -> dict[str, Any] | None:  # noqa: N802
        return money(obj.budget_min_paise)

    def get_budgetMax(self, obj: Campaign) -> dict[str, Any] | None:  # noqa: N802
        return money(obj.budget_max_paise)


class CampaignInput(StrictSerializer):
    title = serializers.CharField(max_length=200)
    brief = serializers.CharField(max_length=5000)
    platform = serializers.ChoiceField(choices=Platform.choices)
    deliverables = serializers.CharField(max_length=200)
    budgetMin = MoneyField()  # noqa: N815
    budgetMax = MoneyField()  # noqa: N815
    closesAt = serializers.DateTimeField()  # noqa: N815
    status = serializers.ChoiceField(choices=[CampaignStatus.DRAFT, CampaignStatus.OPEN], default=CampaignStatus.OPEN)
    usageRights = UsageRightsSerializer()  # noqa: N815


class BidOutput(serializers.ModelSerializer):
    creator = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    expectedViews = serializers.IntegerField(source="expected_views")  # noqa: N815
    deliveryDays = serializers.IntegerField(source="delivery_days")  # noqa: N815
    campaignId = serializers.UUIDField(source="campaign_id")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Bid
        fields = (
            "id",
            "campaignId",
            "creator",
            "price",
            "message",
            "expectedViews",
            "deliveryDays",
            "status",
            "version",
            "createdAt",
        )

    def get_creator(self, obj: Bid) -> dict[str, Any]:
        c = obj.creator
        return {
            "id": str(c.id),
            "displayName": c.display_name,
            "avatarUrl": c.avatar_url,
            "followerCount": c.follower_count,
            "rating": c.rating,
        }

    def get_price(self, obj: Bid) -> dict[str, Any] | None:
        return money(obj.price_paise)


class BidInput(StrictSerializer):
    price = MoneyField()
    message = serializers.CharField(max_length=2000)
    expectedViews = serializers.IntegerField(min_value=0, required=False, allow_null=True)  # noqa: N815
    deliveryDays = serializers.IntegerField(min_value=1, max_value=90)  # noqa: N815


class CampaignSearchParams(StrictSerializer):
    status = serializers.CharField(max_length=20, required=False)
    platform = serializers.ChoiceField(choices=Platform.choices, required=False)
    category = serializers.CharField(max_length=80, required=False)
    industry = serializers.CharField(max_length=80, required=False)
    city = serializers.CharField(max_length=100, required=False)
    territory = serializers.CharField(max_length=100, required=False)
    location = serializers.CharField(max_length=100, required=False)
    budgetMin__gte = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    budgetMax__lte = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    budgetMin = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    budgetMax = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    minBudget = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    maxBudget = serializers.IntegerField(min_value=0, required=False)  # noqa: N815
    search = serializers.CharField(max_length=200, required=False)
    q = serializers.CharField(max_length=200, required=False)
    sort = serializers.CharField(max_length=50, required=False)
    ordering = serializers.CharField(max_length=50, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=50, required=False)
    cursor = serializers.CharField(required=False)
