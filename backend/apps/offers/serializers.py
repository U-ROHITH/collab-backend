from typing import Any

from rest_framework import serializers

from apps.core.serializers import MoneyField, StrictSerializer, UsageRightsSerializer
from apps.creators.serializers import money
from apps.offers.models import Offer


class OfferOutput(serializers.ModelSerializer):
    brand = serializers.SerializerMethodField()
    creator = serializers.SerializerMethodField()
    budget = serializers.SerializerMethodField()
    usageRights = serializers.JSONField(source="usage_rights")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Offer
        fields = (
            "id",
            "brand",
            "creator",
            "deliverable",
            "budget",
            "deadline",
            "message",
            "usageRights",
            "status",
            "createdAt",
        )

    def get_brand(self, obj: Offer) -> dict[str, Any]:
        return {"id": str(obj.brand_id), "name": obj.brand.name, "logoUrl": obj.brand.logo_url}

    def get_creator(self, obj: Offer) -> dict[str, Any]:
        return {"id": str(obj.creator_id), "displayName": obj.creator.display_name}

    def get_budget(self, obj: Offer) -> dict[str, Any] | None:
        return money(obj.budget_paise)


class OfferInput(StrictSerializer):
    creatorId = serializers.UUIDField()  # noqa: N815
    deliverable = serializers.CharField(max_length=200)
    budget = MoneyField()
    deadline = serializers.DateTimeField()
    message = serializers.CharField(max_length=2000)
    usageRights = UsageRightsSerializer()  # noqa: N815


class OfferResponseInput(StrictSerializer):
    status = serializers.ChoiceField(choices=["accepted", "rejected"])
