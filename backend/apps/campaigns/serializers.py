from typing import Any

from rest_framework import serializers

from apps.campaigns.models import Bid, Campaign, CampaignStatus
from apps.core.dates import EndOfDayDateField, LocalDateField
from apps.core.parties import creator_public
from apps.core.serializers import MoneyField, StrictSerializer, UsageRightsSerializer
from apps.creators.models import Platform
from apps.creators.serializers import money
from apps.identity.models import Role


class CampaignOutput(serializers.ModelSerializer):
    """Creators bid blind: `brand` is null for creators. They learn the brand once an order exists."""

    brand = serializers.SerializerMethodField()
    budgetMin = serializers.SerializerMethodField()  # noqa: N815
    budgetMax = serializers.SerializerMethodField()  # noqa: N815
    closesAt = EndOfDayDateField(source="closes_at")  # noqa: N815  "YYYY-MM-DD"
    usageRights = serializers.JSONField(source="usage_rights")  # noqa: N815
    createdAt = LocalDateField(source="created_at")  # noqa: N815  "YYYY-MM-DD"

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

    def get_brand(self, obj: Campaign) -> dict[str, Any] | None:
        request = self.context.get("request")
        if request is None or getattr(request.user, "role", None) != Role.BRAND:
            return None
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
    closesAt = EndOfDayDateField(help_text="YYYY-MM-DD; bidding closes at the end of that day (IST)")  # noqa: N815
    status = serializers.ChoiceField(choices=[CampaignStatus.DRAFT, CampaignStatus.OPEN], default=CampaignStatus.OPEN)
    usageRights = UsageRightsSerializer()  # noqa: N815


class BidOutput(serializers.ModelSerializer):
    creator = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    expectedViews = serializers.IntegerField(source="expected_views")  # noqa: N815
    deliveryDays = serializers.IntegerField(source="delivery_days")  # noqa: N815
    campaignId = serializers.UUIDField(source="campaign_id")  # noqa: N815
    createdAt = LocalDateField(source="created_at")  # noqa: N815  "YYYY-MM-DD"

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
        return creator_public(obj.creator)

    def get_price(self, obj: Bid) -> dict[str, Any] | None:
        return money(obj.price_paise)


class BidInput(StrictSerializer):
    price = MoneyField()
    message = serializers.CharField(max_length=2000)
    expectedViews = serializers.IntegerField(min_value=0, required=False, allow_null=True)  # noqa: N815
    deliveryDays = serializers.IntegerField(min_value=1, max_value=90)  # noqa: N815
