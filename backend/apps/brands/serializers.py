from typing import Any

from rest_framework import serializers

from apps.brands.models import GSTIN_REGEX, Brand, BrandList, CartItem
from apps.core.serializers import StrictSerializer, UsageRightsSerializer
from apps.creators.serializers import CreatorCardOutput, PackageOutput


class BrandOutput(serializers.ModelSerializer):
    legalName = serializers.CharField(source="legal_name")  # noqa: N815
    stateCode = serializers.CharField(source="state_code")  # noqa: N815
    billingAddress = serializers.JSONField(source="billing_address")  # noqa: N815
    logoUrl = serializers.CharField(source="logo_url")  # noqa: N815

    class Meta:
        model = Brand
        fields = ("id", "name", "website", "industry", "logoUrl", "legalName", "gstin", "stateCode", "billingAddress")


class BillingAddressInput(StrictSerializer):
    line1 = serializers.CharField(max_length=200)
    line2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=80)
    state = serializers.CharField(max_length=80)
    pincode = serializers.RegexField(r"^[1-9][0-9]{5}$")


class BrandInput(StrictSerializer):
    name = serializers.CharField(max_length=120, required=False)
    website = serializers.URLField(max_length=300, required=False, allow_blank=True)
    industry = serializers.CharField(max_length=80, required=False, allow_blank=True)
    legalName = serializers.CharField(max_length=200, required=False, allow_blank=True)  # noqa: N815
    gstin = serializers.RegexField(GSTIN_REGEX, required=False, allow_blank=True)
    stateCode = serializers.RegexField(r"^[0-9]{2}$", required=False, allow_blank=True)  # noqa: N815
    billingAddress = BillingAddressInput(required=False)  # noqa: N815


class ListOutput(serializers.ModelSerializer):
    creatorCount = serializers.IntegerField(source="creator_count", read_only=True)  # noqa: N815

    class Meta:
        model = BrandList
        fields = ("id", "name", "creatorCount")


class ListDetailOutput(serializers.ModelSerializer):
    creators = serializers.SerializerMethodField()

    class Meta:
        model = BrandList
        fields = ("id", "name", "creators")

    def get_creators(self, obj: BrandList) -> list[dict[str, Any]]:
        return CreatorCardOutput([i.creator for i in obj.items.select_related("creator")], many=True).data


class ListInput(StrictSerializer):
    name = serializers.CharField(max_length=80)


class ListAddCreatorInput(StrictSerializer):
    creatorId = serializers.UUIDField()  # noqa: N815


class CartItemOutput(serializers.ModelSerializer):
    package = PackageOutput()
    creator = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = ("id", "package", "creator", "brief")

    def get_creator(self, obj: CartItem) -> dict[str, Any]:
        c = obj.package.creator
        return {"id": str(c.id), "displayName": c.display_name, "avatarUrl": c.avatar_url}


class CartAddInput(StrictSerializer):
    packageId = serializers.UUIDField()  # noqa: N815
    brief = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class CheckoutInput(StrictSerializer):
    usageRights = UsageRightsSerializer()  # noqa: N815
