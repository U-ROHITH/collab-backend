from typing import Any

from rest_framework import serializers

from apps.core.dates import EndOfDayDateField, LocalDateField
from apps.core.parties import brand_public, creator_public
from apps.core.serializers import MoneyField, StrictSerializer, UsageRightsSerializer
from apps.creators.serializers import money
from apps.offers.models import Offer


class OfferOutput(serializers.ModelSerializer):
    brand = serializers.SerializerMethodField()
    creator = serializers.SerializerMethodField()
    budget = serializers.SerializerMethodField()
    deadline = EndOfDayDateField()  # "YYYY-MM-DD"
    usageRights = serializers.JSONField(source="usage_rights")  # noqa: N815
    createdAt = LocalDateField(source="created_at")  # noqa: N815  "YYYY-MM-DD"

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
        return brand_public(obj.brand)

    def get_creator(self, obj: Offer) -> dict[str, Any]:
        return creator_public(obj.creator)

    def get_budget(self, obj: Offer) -> dict[str, Any] | None:
        return money(obj.budget_paise)


class OfferInput(StrictSerializer):
    creatorId = serializers.UUIDField()  # noqa: N815
    deliverable = serializers.CharField(max_length=200)
    budget = MoneyField()
    deadline = EndOfDayDateField(help_text="YYYY-MM-DD; due by the end of that day (IST)")
    message = serializers.CharField(max_length=2000)
    usageRights = UsageRightsSerializer()  # noqa: N815


class OfferResponseInput(StrictSerializer):
    status = serializers.ChoiceField(choices=["accepted", "rejected"])
