from typing import Any

from rest_framework import serializers

from apps.core.serializers import HttpsURLField, StrictSerializer
from apps.creators.serializers import money
from apps.orders import state_machine
from apps.orders.models import Deliverable, Order, OrderStatus, Review


class OrderOutput(serializers.ModelSerializer):
    brand = serializers.SerializerMethodField()
    creator = serializers.SerializerMethodField()
    amounts = serializers.SerializerMethodField()
    usageRights = serializers.JSONField(source="usage_rights")  # noqa: N815
    sourceType = serializers.CharField(source="source_type")  # noqa: N815
    deliveryDays = serializers.IntegerField(source="delivery_days")  # noqa: N815
    revisionCount = serializers.IntegerField(source="revision_count")  # noqa: N815
    maxRevisions = serializers.IntegerField(source="max_revisions")  # noqa: N815
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815
    fundedAt = serializers.DateTimeField(source="funded_at")  # noqa: N815
    approvedAt = serializers.DateTimeField(source="approved_at")  # noqa: N815
    completedAt = serializers.DateTimeField(source="completed_at")  # noqa: N815
    allowedActions = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = Order
        fields = (
            "id",
            "status",
            "version",
            "title",
            "deliverables",
            "brief",
            "brand",
            "creator",
            "amounts",
            "usageRights",
            "sourceType",
            "deliveryDays",
            "deadline",
            "revisionCount",
            "maxRevisions",
            "createdAt",
            "fundedAt",
            "approvedAt",
            "completedAt",
            "allowedActions",
        )

    def get_brand(self, obj: Order) -> dict[str, Any]:
        return {"id": str(obj.brand_id), "name": obj.brand.name, "logoUrl": obj.brand.logo_url}

    def get_creator(self, obj: Order) -> dict[str, Any]:
        return {"id": str(obj.creator_id), "displayName": obj.creator.display_name, "avatarUrl": obj.creator.avatar_url}

    def get_amounts(self, obj: Order) -> dict[str, Any]:
        return {
            "subtotal": money(obj.subtotal_paise),
            "platformFee": money(obj.platform_fee_paise),
            "gst": money(obj.gst_paise),
            "tdsDeducted": money(obj.tds_paise),
            "creatorPayout": money(obj.creator_payout_paise),
            "brandTotal": money(obj.brand_total_paise),
        }

    def get_allowedActions(self, obj: Order) -> list[str]:  # noqa: N802
        request = self.context.get("request")
        role = getattr(getattr(request, "user", None), "role", None)
        return state_machine.allowed_events(obj, role) if role else []


class DeliverableOutput(serializers.ModelSerializer):
    contentUrl = serializers.SerializerMethodField()  # noqa: N815
    file = serializers.SerializerMethodField()
    submittedAt = serializers.DateTimeField(source="created_at")  # noqa: N815

    class Meta:
        model = Deliverable
        fields = ("id", "version", "contentUrl", "file", "note", "feedback", "submittedAt")

    def get_contentUrl(self, obj: Deliverable) -> str | None:  # noqa: N802
        return obj.content_url or None

    def get_file(self, obj: Deliverable) -> dict[str, Any] | None:
        u = obj.upload
        if u is None:
            return None
        # Private file: fetch a short-lived link from GET /v1/uploads/{uploadId}/download-url/
        return {"uploadId": str(u.id), "contentType": u.content_type, "size": u.size_bytes}


class DeliverableInput(StrictSerializer):
    """Send exactly one of: contentUrl (https link) or fileUploadId (a finished `deliverable` upload)."""

    contentUrl = HttpsURLField(max_length=1000, required=False)  # noqa: N815
    fileUploadId = serializers.UUIDField(required=False)  # noqa: N815
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if bool(attrs.get("contentUrl")) == bool(attrs.get("fileUploadId")):
            raise serializers.ValidationError("Send exactly one of contentUrl or fileUploadId.")
        return attrs


class FeedbackInput(StrictSerializer):
    feedback = serializers.CharField(max_length=4000)


class ReasonInput(StrictSerializer):
    reason = serializers.CharField(max_length=2000, min_length=5)


class ReviewInput(StrictSerializer):
    stars = serializers.IntegerField(min_value=1, max_value=5)
    text = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")


class ReviewOutput(serializers.ModelSerializer):
    brandName = serializers.CharField(source="brand.name")  # noqa: N815
    date = serializers.DateTimeField(source="created_at")

    class Meta:
        model = Review
        fields = ("id", "stars", "text", "brandName", "date")


class OrderListParams(StrictSerializer):
    status = serializers.ChoiceField(choices=OrderStatus.choices, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=50, required=False)
    cursor = serializers.CharField(max_length=500, required=False)
