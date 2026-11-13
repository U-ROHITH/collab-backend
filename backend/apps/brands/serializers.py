from typing import Any

from rest_framework import serializers

from apps.brands.models import GSTIN_REGEX, Brand, BrandList, CartItem
from apps.core.serializers import StrictSerializer, UsageRightsSerializer
from apps.creators.serializers import CreatorCardOutput, PackageOutput


class BrandOutput(serializers.ModelSerializer):
    legalName = serializers.CharField(source="legal_name")  # noqa: N815
    stateCode = serializers.CharField(source="state_code")  # noqa: N815
    billingAddress = serializers.SerializerMethodField()  # noqa: N815
    billingEmail = serializers.SerializerMethodField()  # noqa: N815
    logoUrl = serializers.CharField(source="logo_url")  # noqa: N815

    class Meta:
        model = Brand
        fields = (
            "id",
            "name",
            "website",
            "industry",
            "logoUrl",
            "legalName",
            "gstin",
            "stateCode",
            "billingAddress",
            "billingEmail",
        )

    def get_billingAddress(self, obj: Brand) -> Any:  # noqa: N802
        if isinstance(obj.billing_address, dict) and "raw" in obj.billing_address:
            return obj.billing_address["raw"]
        return obj.billing_address

    def get_billingEmail(self, obj: Brand) -> str:  # noqa: N802
        if isinstance(obj.billing_address, dict):
            return obj.billing_address.get("email") or getattr(obj.owner, "email", "") or ""
        return getattr(obj.owner, "email", "") or ""


class BillingAddressInput(StrictSerializer):
    line1 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    line2 = serializers.CharField(max_length=200, required=False, allow_blank=True)
    city = serializers.CharField(max_length=80, required=False, allow_blank=True)
    state = serializers.CharField(max_length=80, required=False, allow_blank=True)
    pincode = serializers.CharField(max_length=10, required=False, allow_blank=True)
    country = serializers.CharField(max_length=80, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)


class BrandInput(StrictSerializer):
    name = serializers.CharField(max_length=120, required=False)
    website = serializers.CharField(max_length=300, required=False, allow_blank=True)
    industry = serializers.CharField(max_length=80, required=False, allow_blank=True)
    category = serializers.CharField(max_length=80, required=False, allow_blank=True)
    legalName = serializers.CharField(max_length=200, required=False, allow_blank=True)  # noqa: N815
    gstin = serializers.CharField(max_length=20, required=False, allow_blank=True)
    stateCode = serializers.CharField(max_length=10, required=False, allow_blank=True)  # noqa: N815
    billingAddress = serializers.JSONField(required=False)  # noqa: N815
    billingEmail = serializers.EmailField(required=False, allow_blank=True, allow_null=True)  # noqa: N815

    def validate_website(self, value: str) -> str:
        value = value.strip()
        if value and not (value.startswith("http://") or value.startswith("https://")):
            return f"https://{value}"
        return value


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
