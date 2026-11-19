import re
from typing import Any

from rest_framework import serializers

from apps.brands.models import Brand, BrandList, CartItem
from apps.core.serializers import StrictSerializer, UsageRightsSerializer
from apps.creators.serializers import CreatorCardOutput, PackageOutput

_PAN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_CIN = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")


class BrandBriefSerializer(serializers.ModelSerializer):
    userId = serializers.UUIDField(source="owner_id", read_only=True)  # noqa: N815
    logoUrl = serializers.CharField(source="logo_url", read_only=True, allow_blank=True)  # noqa: N815
    accountStatus = serializers.CharField(source="owner.status", read_only=True)  # noqa: N815

    class Meta:
        model = Brand
        fields = ("id", "userId", "name", "logoUrl", "accountStatus")


class BrandOutput(serializers.ModelSerializer):
    userId = serializers.UUIDField(source="owner_id", read_only=True)  # noqa: N815
    legalName = serializers.CharField(source="legal_name")  # noqa: N815
    stateCode = serializers.CharField(source="state_code")  # noqa: N815
    billingAddress = serializers.SerializerMethodField()  # noqa: N815
    billingEmail = serializers.SerializerMethodField()  # noqa: N815
    # The same logo under every name the frontend reads.
    logoUrl = serializers.SerializerMethodField(method_name="get_logo")  # noqa: N815
    avatarUrl = serializers.SerializerMethodField(method_name="get_logo")  # noqa: N815
    logo_url = serializers.SerializerMethodField(method_name="get_logo")
    avatar_url = serializers.SerializerMethodField(method_name="get_logo")
    avatar = serializers.SerializerMethodField(method_name="get_logo")
    image = serializers.SerializerMethodField(method_name="get_logo")
    companyPan = serializers.SerializerMethodField()  # noqa: N815
    cin = serializers.CharField(allow_blank=True, allow_null=True)
    gstinStatus = serializers.CharField(source="gstin_status")  # noqa: N815
    regVerified = serializers.BooleanField(source="reg_verified")  # noqa: N815
    signatoryName = serializers.CharField(source="signatory_name", allow_blank=True, allow_null=True)  # noqa: N815
    signatoryDesignation = serializers.CharField(  # noqa: N815
        source="signatory_designation", allow_blank=True, allow_null=True
    )

    class Meta:
        model = Brand
        fields = (
            "id",
            "userId",
            "name",
            "website",
            "industry",
            "logoUrl",
            "avatarUrl",
            "logo_url",
            "avatar_url",
            "avatar",
            "image",
            "legalName",
            "gstin",
            "stateCode",
            "billingAddress",
            "billingEmail",
            "companyPan",
            "cin",
            "gstinStatus",
            "regVerified",
            "signatoryName",
            "signatoryDesignation",
        )

    def get_companyPan(self, obj: Brand) -> str | None:  # noqa: N802
        if not obj.company_pan:
            return None
        return f"XXXXXX{obj.company_pan[-4:]}"

    def get_logo(self, obj: Brand) -> str | None:
        return obj.logo_url or None

    def get_billingAddress(self, obj: Brand) -> Any:  # noqa: N802
        if isinstance(obj.billing_address, dict) and "raw" in obj.billing_address:
            return obj.billing_address["raw"]
        return obj.billing_address

    def get_billingEmail(self, obj: Brand) -> str:  # noqa: N802
        if isinstance(obj.billing_address, dict):
            return obj.billing_address.get("email") or getattr(obj.owner, "email", "") or ""
        return getattr(obj.owner, "email", "") or ""


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
    logoUrl = serializers.CharField(max_length=600, required=False, allow_blank=True, allow_null=True)  # noqa: N815
    logo = serializers.CharField(max_length=600, required=False, allow_blank=True, allow_null=True)
    avatarUrl = serializers.CharField(max_length=600, required=False, allow_blank=True, allow_null=True)  # noqa: N815
    logoUploadId = serializers.UUIDField(required=False, allow_null=True)  # noqa: N815
    companyPan = serializers.CharField(max_length=10, required=False, allow_blank=True)  # noqa: N815
    cin = serializers.CharField(max_length=21, required=False, allow_blank=True)
    gstinStatus = serializers.CharField(max_length=20, required=False, allow_blank=True)  # noqa: N815
    regVerified = serializers.BooleanField(required=False)  # noqa: N815
    signatoryName = serializers.CharField(max_length=120, required=False, allow_blank=True)  # noqa: N815
    signatoryDesignation = serializers.CharField(max_length=80, required=False, allow_blank=True)  # noqa: N815

    def validate_website(self, value: str) -> str:
        value = value.strip()
        if value and not (value.startswith("http://") or value.startswith("https://")):
            return f"https://{value}"
        return value

    def validate_companyPan(self, value: str) -> str:  # noqa: N802
        val = value.strip().upper()
        if val and not _PAN.match(val):
            raise serializers.ValidationError("Enter a valid 10-character PAN (e.g. AABCB1234F).")
        return val

    def validate_cin(self, value: str) -> str:
        val = value.strip().upper()
        if val and not _CIN.match(val):
            raise serializers.ValidationError("Enter a valid 21-character CIN (e.g. U12345MH2020PTC123456).")
        return val


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
        from apps.identity.models import UserStatus

        items = obj.items.select_related("creator", "creator__user")
        active_creators = [
            i.creator
            for i in items
            if not i.creator.user or i.creator.user.status == UserStatus.ACTIVE
        ]
        return CreatorCardOutput(active_creators, many=True).data


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
        return {
            "id": str(c.id),
            "displayName": c.display_name,
            "avatarUrl": c.avatar_url or None,
            "avatar_url": c.avatar_url or None,
            "avatar": c.avatar_url or None,
            "image": c.avatar_url or None,
        }


class CartAddInput(StrictSerializer):
    packageId = serializers.UUIDField()  # noqa: N815
    brief = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class CheckoutInput(StrictSerializer):
    usageRights = UsageRightsSerializer()  # noqa: N815
