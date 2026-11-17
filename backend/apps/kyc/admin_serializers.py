from typing import Any

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.kyc.models import VerificationRequest


class AdminVerificationCheckOutput(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    result = serializers.ChoiceField(choices=["pass", "warn", "fail"])
    text = serializers.CharField()
    blocking = serializers.BooleanField()


class AdminVerificationUserOutput(serializers.Serializer):
    id = serializers.CharField()
    fullName = serializers.CharField(source="full_name")  # noqa: N815
    email = serializers.CharField(allow_null=True)
    phone = serializers.CharField(allow_null=True)
    role = serializers.CharField()


class AdminVerificationListOutput(serializers.ModelSerializer):
    user = AdminVerificationUserOutput()
    displayName = serializers.SerializerMethodField()  # noqa: N815
    slaDueAt = serializers.DateTimeField(source="sla_due_at")  # noqa: N815
    submittedAt = serializers.DateTimeField(source="submitted_at")  # noqa: N815
    decidedAt = serializers.DateTimeField(source="decided_at", allow_null=True)  # noqa: N815
    checksSummary = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = VerificationRequest
        fields = (
            "id",
            "kind",
            "status",
            "user",
            "displayName",
            "slaDueAt",
            "submittedAt",
            "decidedAt",
            "checksSummary",
        )

    def get_displayName(self, obj: VerificationRequest) -> str:  # noqa: N802
        u = obj.user
        if obj.kind == "creator" and getattr(u, "creator", None):
            return u.creator.display_name or u.full_name or ""
        if obj.kind == "brand" and getattr(u, "brand", None):
            return u.brand.name or u.full_name or ""
        return u.full_name or u.email or u.phone or ""

    def get_checksSummary(self, obj: VerificationRequest) -> dict[str, Any]:  # noqa: N802
        checks = obj.checks or []
        passed = sum(1 for c in checks if c.get("result") == "pass")
        warned = sum(1 for c in checks if c.get("result") == "warn")
        failed = sum(1 for c in checks if c.get("result") == "fail")
        has_blocking = any(c.get("blocking") is True for c in checks)
        return {
            "total": len(checks),
            "passed": passed,
            "warned": warned,
            "failed": failed,
            "hasBlocking": has_blocking,
        }


class AdminVerificationDetailOutput(serializers.ModelSerializer):
    user = AdminVerificationUserOutput()
    decidedBy = serializers.SerializerMethodField()  # noqa: N815
    slaDueAt = serializers.DateTimeField(source="sla_due_at")  # noqa: N815
    submittedAt = serializers.DateTimeField(source="submitted_at")  # noqa: N815
    decidedAt = serializers.DateTimeField(source="decided_at", allow_null=True)  # noqa: N815
    decision = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()
    kyc = serializers.SerializerMethodField()
    checks = AdminVerificationCheckOutput(many=True)
    socials = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRequest
        fields = (
            "id",
            "kind",
            "status",
            "user",
            "slaDueAt",
            "submittedAt",
            "decidedAt",
            "decidedBy",
            "decision",
            "profile",
            "kyc",
            "checks",
            "socials",
        )

    def get_decidedBy(self, obj: VerificationRequest) -> dict[str, Any] | None:  # noqa: N802
        if not obj.decided_by:
            return None
        db = obj.decided_by
        return {"id": str(db.id), "fullName": db.full_name, "email": db.email, "role": db.role}

    def get_decision(self, obj: VerificationRequest) -> dict[str, Any]:
        return {
            "reason": obj.decision_reason or None,
            "note": obj.decision_note or None,
            "requestedItems": list(obj.requested_items or []),
            "message": obj.decision_reason or None,
        }

    def get_profile(self, obj: VerificationRequest) -> dict[str, Any]:
        u = obj.user
        if obj.kind == "creator":
            c = getattr(u, "creator", None)
            if not c:
                return {}
            return {
                "id": str(c.id),
                "displayName": c.display_name,
                "categories": list(c.categories or []),
                "primaryPlatform": c.primary_platform,
                "followerCount": c.follower_count,
                "dob": c.dob.isoformat() if c.dob else None,
                "gender": c.gender,
                "location": c.location,
                "avatarUrl": c.avatar_url or None,
            }
        b = getattr(u, "brand", None)
        if not b:
            return {}
        return {
            "id": str(b.id),
            "name": b.name,
            "legalName": b.legal_name,
            "gstin": b.gstin,
            "companyPan": f"XXXXXX{b.company_pan[-4:]}" if b.company_pan else None,
            "cin": b.cin or None,
            "gstinStatus": b.gstin_status,
            "regVerified": b.reg_verified,
            "signatoryName": b.signatory_name or None,
            "signatoryDesignation": b.signatory_designation or None,
            "billingEmail": (b.billing_address or {}).get("email") if isinstance(b.billing_address, dict) else None,
            "website": b.website or None,
            "logoUrl": b.logo_url or None,
        }

    def get_kyc(self, obj: VerificationRequest) -> dict[str, Any]:
        k = obj.kyc_profile
        doc_url = None
        if k.id_document:
            from apps.uploads import storage

            try:
                doc_url = storage.signed_download_url(k.id_document.bucket, k.id_document.storage_key, 300)
            except Exception:
                doc_url = None

        return {
            "pan": {
                "name": k.pan_name or None,
                "last4": k.pan_last4 or None,
                "masked": f"XXXXXX{k.pan_last4}" if k.pan_last4 else None,
            },
            "idDocument": {
                "idType": k.id_type or None,
                "uploadId": str(k.id_document_id) if k.id_document_id else None,
                "url": doc_url,
            },
            "bank": {
                "holderName": k.bank_holder_name or None,
                "last4": k.bank_account_last4 or None,
                "masked": f"XXXXXX{k.bank_account_last4}" if k.bank_account_last4 else None,
                "ifsc": k.bank_ifsc or None,
            },
        }

    def get_socials(self, obj: VerificationRequest) -> dict[str, Any]:
        u = obj.user
        if obj.kind == "creator":
            c = getattr(u, "creator", None)
            if not c:
                return {}
            links = c.social_media_links if isinstance(c.social_media_links, dict) else {}
            stats = c.platform_stats if isinstance(c.platform_stats, dict) else {}
            return {"links": links, "stats": stats}
        b = getattr(u, "brand", None)
        return {"website": b.website} if b and b.website else {}


class AdminVerificationRejectInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=500)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000, default="")


class AdminVerificationRequestInfoInput(StrictSerializer):
    items = serializers.ListField(child=serializers.CharField(max_length=40), min_length=1)
    message = serializers.CharField(min_length=4, max_length=1000)


class AdminAuditOutput(serializers.Serializer):
    id = serializers.CharField()
    hash = serializers.CharField()


class AdminVerificationActionResult(serializers.Serializer):
    verification = AdminVerificationDetailOutput()
    audit = AdminAuditOutput()
