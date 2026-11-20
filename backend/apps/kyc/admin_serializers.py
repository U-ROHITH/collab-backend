import logging
from typing import Any

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.kyc.models import VerificationRequest

logger = logging.getLogger(__name__)


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


def _signed_url_for_upload(upload: Any) -> str | None:
    if not upload:
        return None
    if getattr(upload, "public_url", None):
        return upload.public_url
    from apps.uploads import storage

    try:
        url = storage.signed_download_url(upload.bucket, upload.storage_key, 300)
        if url:
            return url
    except Exception as exc:  # noqa: BLE001
        logger.debug("Signed download URL generation fallback: %s", exc)
    return f"/v1/uploads/{upload.id}/download-url/"


def _extract_social_accounts(c: Any) -> list[dict[str, Any]]:
    if not c:
        return []
    accounts: list[dict[str, Any]] = []
    seen_platforms = set()
    if isinstance(getattr(c, "social_accounts", None), list):
        for acc in c.social_accounts:
            if isinstance(acc, dict):
                p = acc.get("platform")
                if p:
                    seen_platforms.add(p)
                accounts.append(acc)

    links = c.social_media_links if isinstance(getattr(c, "social_media_links", None), dict) else {}
    stats = c.platform_stats if isinstance(getattr(c, "platform_stats", None), dict) else {}

    for platform, handle in links.items():
        if platform == "languages" or not handle:
            continue
        if platform in seen_platforms:
            continue
        st = stats.get(platform, {}) if isinstance(stats.get(platform), dict) else {}
        followers = (
            st.get("followersCount")
            or st.get("followers")
            or (c.follower_count if platform == c.primary_platform else 0)
        )
        url = str(handle) if str(handle).startswith("http") else f"https://{platform}.com/{handle}"
        accounts.append({
            "platform": platform,
            "handle": str(handle),
            "url": url,
            "followers": followers,
            "followersCount": followers,
            "engagementRate": st.get("engagementRate"),
            "avgViews": st.get("avgViews"),
            "verified": c.verified if platform == c.primary_platform else False,
        })
        seen_platforms.add(platform)

    for platform, st in stats.items():
        if platform in seen_platforms or not isinstance(st, dict):
            continue
        followers = st.get("followersCount") or st.get("followers") or 0
        accounts.append({
            "platform": platform,
            "handle": "",
            "url": "",
            "followers": followers,
            "followersCount": followers,
            "engagementRate": st.get("engagementRate"),
            "avgViews": st.get("avgViews"),
            "verified": c.verified if platform == c.primary_platform else False,
        })
        seen_platforms.add(platform)

    return accounts


class AdminVerificationDetailOutput(serializers.ModelSerializer):
    user = AdminVerificationUserOutput()
    userId = serializers.CharField(source="user_id")  # noqa: N815
    account = serializers.SerializerMethodField()
    decidedBy = serializers.SerializerMethodField()  # noqa: N815
    slaDueAt = serializers.DateTimeField(source="sla_due_at")  # noqa: N815
    submittedAt = serializers.DateTimeField(source="submitted_at")  # noqa: N815
    decidedAt = serializers.DateTimeField(source="decided_at", allow_null=True)  # noqa: N815
    decision = serializers.SerializerMethodField()
    profile = serializers.SerializerMethodField()
    kyc = serializers.SerializerMethodField()
    checks = AdminVerificationCheckOutput(many=True)
    socials = serializers.SerializerMethodField()
    social_accounts = serializers.SerializerMethodField()  # noqa: N815
    socialAccounts = serializers.SerializerMethodField()  # noqa: N815
    id_doc = serializers.SerializerMethodField()  # noqa: N815
    idDoc = serializers.SerializerMethodField()  # noqa: N815
    pan = serializers.SerializerMethodField()
    bank = serializers.SerializerMethodField()
    selfie = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()

    class Meta:
        model = VerificationRequest
        fields = (
            "id",
            "kind",
            "status",
            "user",
            "userId",
            "account",
            "slaDueAt",
            "submittedAt",
            "decidedAt",
            "decidedBy",
            "decision",
            "profile",
            "kyc",
            "checks",
            "socials",
            "social_accounts",
            "socialAccounts",
            "id_doc",
            "idDoc",
            "pan",
            "bank",
            "selfie",
            "documents",
        )

    def get_account(self, obj: VerificationRequest) -> str | None:
        u = obj.user
        if obj.kind == "creator":
            from apps.creators.models import Creator

            c = getattr(u, "creator", None) or Creator.objects.filter(user=u).first()
            return str(c.id) if c else None
        from apps.brands.models import Brand

        b = getattr(u, "brand", None) or Brand.objects.filter(owner=u).first()
        return str(b.id) if b else None

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
            from apps.creators.models import Creator

            c = getattr(u, "creator", None) or Creator.objects.filter(user=u).first()
            if not c and getattr(u, "role", None) == "creator":
                from apps.creators.services import create_creator_for_user

                display_name = u.full_name or (u.email or "").split("@")[0] or "Creator"
                c = create_creator_for_user(user=u, display_name=display_name)
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
        from apps.brands.models import Brand

        b = getattr(u, "brand", None) or Brand.objects.filter(owner=u).first()
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
        doc_url = _signed_url_for_upload(k.id_document) if (k and k.id_document) else None

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
            from apps.creators.models import Creator

            c = getattr(u, "creator", None) or Creator.objects.filter(user=u).first()
            if not c:
                return {"links": {}, "stats": {}, "accounts": [], "items": [], "list": []}
            links = c.social_media_links if isinstance(c.social_media_links, dict) else {}
            stats = c.platform_stats if isinstance(c.platform_stats, dict) else {}
            accounts = _extract_social_accounts(c)
            return {
                "links": links,
                "stats": stats,
                "accounts": accounts,
                "items": accounts,
                "list": accounts,
            }
        from apps.brands.models import Brand

        b = getattr(u, "brand", None) or Brand.objects.filter(owner=u).first()
        website = b.website if b else None
        return {
            "website": website,
            "links": {"website": website} if website else {},
            "accounts": [{"platform": "website", "url": website}] if website else [],
        }

    def get_social_accounts(self, obj: VerificationRequest) -> list[dict[str, Any]]:  # noqa: N802
        u = obj.user
        if obj.kind == "creator":
            from apps.creators.models import Creator

            c = getattr(u, "creator", None) or Creator.objects.filter(user=u).first()
            return _extract_social_accounts(c)
        return []

    def get_socialAccounts(self, obj: VerificationRequest) -> list[dict[str, Any]]:  # noqa: N802
        return self.get_social_accounts(obj)

    def get_id_doc(self, obj: VerificationRequest) -> dict[str, Any] | None:
        k = obj.kyc_profile
        if not k:
            return None
        doc_url = _signed_url_for_upload(k.id_document) if k.id_document else None
        u = obj.user
        from apps.creators.models import Creator

        c = getattr(u, "creator", None) or (Creator.objects.filter(user=u).first() if obj.kind == "creator" else None)
        name_check = next((chk for chk in (obj.checks or []) if chk.get("key") == "name_match"), None)
        match_status = name_check.get("result") if name_check else "pass"
        dob = c.dob.isoformat() if (c and c.dob) else None
        name = k.pan_name or (c.display_name if c else u.full_name) or None
        masked_number = f"XXXXXX{k.pan_last4}" if k.pan_last4 else None
        return {
            "type": k.id_type or None,
            "idType": k.id_type or None,
            "uploadId": str(k.id_document_id) if k.id_document_id else None,
            "url": doc_url,
            "downloadUrl": doc_url,
            "name": name,
            "dob": dob,
            "maskedNumber": masked_number,
            "matchStatus": match_status,
            "status": match_status,
        }

    def get_idDoc(self, obj: VerificationRequest) -> dict[str, Any] | None:  # noqa: N802
        return self.get_id_doc(obj)

    def get_pan(self, obj: VerificationRequest) -> dict[str, Any] | None:
        k = obj.kyc_profile
        if not k:
            return None
        name_check = next((chk for chk in (obj.checks or []) if chk.get("key") == "name_match"), None)
        match_status = name_check.get("result") if name_check else ("pass" if k.pan_verified else "pending")
        return {
            "name": k.pan_name or None,
            "holderName": k.pan_name or None,
            "last4": k.pan_last4 or None,
            "masked": f"XXXXXX{k.pan_last4}" if k.pan_last4 else None,
            "verified": k.pan_verified,
            "matchStatus": match_status,
            "status": match_status,
        }

    def get_bank(self, obj: VerificationRequest) -> dict[str, Any] | None:
        k = obj.kyc_profile
        if not k:
            return None
        bank_check = next((chk for chk in (obj.checks or []) if chk.get("key") == "bank_name_match"), None)
        bank_status = bank_check.get("result") if bank_check else ("pass" if k.bank_verified else "pending")
        u = obj.user
        from apps.creators.models import Creator

        c = getattr(u, "creator", None) or (Creator.objects.filter(user=u).first() if obj.kind == "creator" else None)
        upi_id = None
        payout_method = "bank_transfer"
        if c:
            from apps.money.models import WithdrawalRequest

            w = WithdrawalRequest.objects.filter(creator=c).order_by("-created_at").first()
            if w:
                upi_id = getattr(w, "upi_id", None)
                payout_method = getattr(w, "payout_method", "bank_transfer")
        return {
            "holderName": k.bank_holder_name or None,
            "accountHolder": k.bank_holder_name or None,
            "last4": k.bank_account_last4 or None,
            "accountNumber": f"XXXXXX{k.bank_account_last4}" if k.bank_account_last4 else None,
            "masked": f"XXXXXX{k.bank_account_last4}" if k.bank_account_last4 else None,
            "ifsc": k.bank_ifsc or None,
            "upiId": upi_id,
            "payoutMethod": payout_method,
            "verified": k.bank_verified,
            "matchStatus": bank_status,
            "status": bank_status,
        }

    def get_selfie(self, obj: VerificationRequest) -> dict[str, Any] | None:
        from apps.uploads.models import Upload

        up = Upload.objects.filter(owner=obj.user, kind__in=["selfie", "liveness"]).first()
        if up:
            url = _signed_url_for_upload(up)
            return {
                "uploadId": str(up.id),
                "url": url,
                "downloadUrl": url,
                "status": "pass",
            }
        return None

    def get_documents(self, obj: VerificationRequest) -> list[dict[str, Any]]:
        docs = []
        k = obj.kyc_profile
        if k and k.id_document:
            up = k.id_document
            url = _signed_url_for_upload(up)
            docs.append({
                "id": str(up.id),
                "uploadId": str(up.id),
                "type": k.id_type or "id_document",
                "kind": "id_document",
                "name": getattr(up, "original_filename", "") or f"{k.id_type or 'id_document'}.pdf",
                "fileName": getattr(up, "original_filename", "") or f"{k.id_type or 'id_document'}.pdf",
                "url": url,
                "downloadUrl": url,
                "contentType": getattr(up, "content_type", "")
                or getattr(up, "declared_content_type", "application/pdf"),
                "size": getattr(up, "size_bytes", 0) or getattr(up, "declared_size", 0),
            })
        from apps.uploads.models import Upload, UploadKind

        exclude_ids = [k.id_document_id] if (k and k.id_document_id) else []
        other_uploads = Upload.objects.filter(owner=obj.user, kind=UploadKind.KYC_DOCUMENT).exclude(id__in=exclude_ids)
        for up in other_uploads:
            url = _signed_url_for_upload(up)
            docs.append({
                "id": str(up.id),
                "uploadId": str(up.id),
                "type": "kyc_document",
                "kind": "kyc_document",
                "name": getattr(up, "original_filename", "") or "kyc_document.pdf",
                "fileName": getattr(up, "original_filename", "") or "kyc_document.pdf",
                "url": url,
                "downloadUrl": url,
                "contentType": getattr(up, "content_type", "")
                or getattr(up, "declared_content_type", "application/pdf"),
                "size": getattr(up, "size_bytes", 0) or getattr(up, "declared_size", 0),
            })
        return docs


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
