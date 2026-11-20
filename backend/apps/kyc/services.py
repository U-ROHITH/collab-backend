import re
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core import crypto
from apps.core.exceptions import Conflict, DomainError, Forbidden
from apps.identity.models import AuditLog, User
from apps.kyc.models import KycProfile, KycStatus
from apps.uploads.models import Upload, UploadKind, UploadStatus

_EDITABLE = (KycStatus.NOT_STARTED, KycStatus.IN_PROGRESS, KycStatus.REJECTED, KycStatus.ACTION_NEEDED)


class KycRequired(Forbidden):
    code = "KYC_REQUIRED"
    message = "Complete KYC verification to do this."


def status_of(user: User | None) -> str:
    kyc = getattr(user, "kyc", None) if user else None
    return kyc.status if kyc else KycStatus.NOT_STARTED


def require_verified(user: User) -> None:
    """Gate for a creator's money-moving actions. Raises 403 KYC_REQUIRED unless verified (or enforcement is off).
    Brand KYC is voluntary (refunds/compliance only), so brands are never blocked."""
    if getattr(user, "role", None) == "brand":
        return
    if settings.KYC_ENFORCED and status_of(user) != KycStatus.VERIFIED:
        raise KycRequired()


class CounterpartyKycPending(DomainError):
    code = "COUNTERPARTY_KYC_PENDING"
    message = "The other party hasn't finished KYC verification yet."


def require_order_parties_verified(*, actor: User, brand_owner: User | None, creator_user: User | None) -> None:
    """Every order needs both sides verified, whichever route creates it (bid select, offer accept,
    checkout). The caller gets 403 KYC_REQUIRED for their own KYC, 422 COUNTERPARTY_KYC_PENDING for the other side's."""
    if not settings.KYC_ENFORCED:
        return
    for party in (creator_user,):  # brand KYC is voluntary: only the creator (who gets paid) must be verified
        if party is None or status_of(party) != KycStatus.VERIFIED:
            if party is not None and party.pk == actor.pk:
                raise KycRequired()
            role = "creator" if party is creator_user else "brand"
            raise CounterpartyKycPending(f"This {role} hasn't finished KYC verification yet.")


def get_or_create(user: User) -> KycProfile:
    kyc, _ = KycProfile.objects.get_or_create(user=user)
    return kyc


def _locked_for_edit(user: User) -> KycProfile:
    kyc = KycProfile.objects.select_for_update().get(pk=get_or_create(user).pk)
    if kyc.status not in _EDITABLE:
        msg = (
            "KYC is under review and can't be changed right now."
            if kyc.status == KycStatus.PENDING
            else "KYC is verified. Contact support to change these details."
        )
        raise Conflict(msg, code="KYC_LOCKED")
    return kyc


def _save_step(kyc: KycProfile, fields: list[str]) -> KycProfile:
    if kyc.status == KycStatus.NOT_STARTED:
        kyc.status = KycStatus.IN_PROGRESS
    kyc.save(update_fields=[*fields, "status", "updated_at"])
    return kyc


def save_pan(*, user: User, pan: str, name_on_pan: str) -> KycProfile:
    with transaction.atomic():
        kyc = _locked_for_edit(user)
        kyc.pan_name = name_on_pan
        kyc.pan_last4 = pan[-4:]
        kyc.pan_ciphertext = crypto.encrypt(pan, context=f"kyc.pan:{user.pk}")
        return _save_step(kyc, ["pan_name", "pan_last4", "pan_ciphertext"])


def save_id(*, user: User, id_type: str, upload_id: UUID) -> KycProfile:
    upload = Upload.objects.filter(
        pk=upload_id, owner=user, kind=UploadKind.KYC_DOCUMENT, status=UploadStatus.READY
    ).first()
    if upload is None:
        raise DomainError("Upload the ID document first (kind: kyc_document).", code="KYC_DOCUMENT_INVALID")
    with transaction.atomic():
        kyc = _locked_for_edit(user)
        kyc.id_type = id_type
        kyc.id_document = upload
        return _save_step(kyc, ["id_type", "id_document"])


def save_bank(*, user: User, holder_name: str, account_number: str, ifsc: str) -> KycProfile:
    with transaction.atomic():
        kyc = _locked_for_edit(user)
        kyc.bank_holder_name = holder_name
        kyc.bank_account_last4 = account_number[-4:]
        kyc.bank_account_ciphertext = crypto.encrypt(account_number, context=f"kyc.bank:{user.pk}")
        kyc.bank_ifsc = ifsc
        return _save_step(kyc, ["bank_holder_name", "bank_account_last4", "bank_account_ciphertext", "bank_ifsc"])


_CHECK_LABELS = {
    "age_18_plus": "Age 18+",
    "name_match": "PAN vs Profile Name",
    "gstin_pan_match": "GSTIN PAN Match",
    "business_email_domain": "Business Email Domain",
    "bank_name_match": "Bank Holder Name Match",
}
_CONSUMER_DOMAINS = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "icloud.com",
        "rediffmail.com",
        "aol.com",
        "protonmail.com",
    }
)


def _check(key: str, result: str, text: str) -> dict[str, Any]:
    """Only failures block approval."""
    return {"key": key, "label": _CHECK_LABELS[key], "result": result, "text": text, "blocking": result == "fail"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _similar(a: str, b: str) -> bool:
    return a == b or a in b or b in a


def _age_check(creator: Any) -> dict[str, Any]:
    dob = getattr(creator, "dob", None)
    if dob is None:
        return _check("age_18_plus", "warn", "Date of birth not provided on creator profile")
    today = timezone.now().date()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age >= 18:
        return _check("age_18_plus", "pass", f"Creator is {age} years old (DOB: {dob})")
    return _check("age_18_plus", "fail", f"Creator is under 18 ({age} years old; DOB: {dob})")


def _name_check(pan_name: str, profile_name: str) -> dict[str, Any]:
    if not _norm(pan_name):
        return _check("name_match", "fail", "Name on PAN is missing")
    if not _norm(profile_name):
        return _check("name_match", "warn", "Profile name is not set")
    if _similar(_norm(pan_name), _norm(profile_name)):
        return _check("name_match", "pass", f"PAN name '{pan_name}' matches profile name '{profile_name}'")
    return _check("name_match", "warn", f"PAN name '{pan_name}' differs from profile name '{profile_name}'")


def _gstin_check(user: User, kyc: KycProfile, brand: Any) -> dict[str, Any]:
    gstin = (brand.gstin if brand else "").strip().upper()
    if not gstin:
        return _check("gstin_pan_match", "warn", "No GSTIN provided for brand")
    if len(gstin) != 15:
        return _check("gstin_pan_match", "fail", f"Invalid GSTIN length ({len(gstin)} characters)")
    if brand and brand.company_pan:
        pan = brand.company_pan.strip().upper()
    else:
        try:
            pan = crypto.decrypt(kyc.pan_ciphertext, context=f"kyc.pan:{user.pk}").strip().upper()
        except Exception:  # noqa: BLE001 - no readable PAN means "nothing to compare"
            pan = ""
    if not pan:
        return _check("gstin_pan_match", "warn", f"GSTIN provided ({gstin}) but no PAN found to compare")
    if gstin[2:12] == pan:
        return _check("gstin_pan_match", "pass", f"PAN {pan} matches GSTIN characters 3..12")
    return _check("gstin_pan_match", "fail", f"PAN {pan} does not match GSTIN {gstin} (expected {gstin[2:12]})")


def _email_check(user: User, brand: Any) -> dict[str, Any]:
    billing = brand.billing_address if brand and isinstance(brand.billing_address, dict) else {}
    email = (billing.get("email") or user.email or "").strip().lower()
    domain = email.split("@")[-1] if "@" in email else ""
    if domain and domain not in _CONSUMER_DOMAINS:
        return _check("business_email_domain", "pass", f"Business email domain: {domain}")
    return _check("business_email_domain", "warn", f"Consumer email domain: {domain or 'none'}")


def run_automated_checks(user: User, kyc: KycProfile) -> list[dict[str, Any]]:
    """Advisory checks shown on the verification desk; a failing one blocks approval."""
    from apps.identity.models import Role

    brand = getattr(user, "brand", None) if user.role == Role.BRAND else None
    pan_name = (kyc.pan_name or "").strip()
    profile_name = ((brand and (brand.signatory_name or brand.legal_name)) or user.full_name or "").strip()
    checks = []
    if user.role == Role.CREATOR:
        checks.append(_age_check(getattr(user, "creator", None)))
    checks.append(_name_check(pan_name, profile_name))
    if user.role == Role.BRAND:
        checks += [_gstin_check(user, kyc, brand), _email_check(user, brand)]
    holder = (kyc.bank_holder_name or "").strip()
    if not _norm(holder):
        checks.append(_check("bank_name_match", "fail", "Bank account holder name missing"))
    elif _norm(pan_name):
        match = _similar(_norm(holder), _norm(pan_name))
        verb = "matches" if match else "differs from"
        checks.append(
            _check(
                "bank_name_match",
                "pass" if match else "warn",
                f"Bank holder name '{holder}' {verb} PAN name '{pan_name}'",
            )
        )
    return checks


def submit(*, user: User) -> KycProfile:

    with transaction.atomic():
        kyc = _locked_for_edit(user)
        steps = {"pan": kyc.pan_done, "id": kyc.id_done, "bank": kyc.bank_done}
        missing = [name for name, done in steps.items() if not done]
        if missing:
            raise DomainError(f"Finish these steps first: {', '.join(missing)}.", code="KYC_INCOMPLETE")
        kyc.status = KycStatus.PENDING
        kyc.submitted_at = timezone.now()
        kyc.rejection_reason = ""
        kyc.review_message = ""
        kyc.requested_items = []
        kyc.save(
            update_fields=[
                "status",
                "submitted_at",
                "rejection_reason",
                "review_message",
                "requested_items",
                "updated_at",
            ]
        )

        open_verification(user, kyc)

    return kyc


def open_verification(user: User, kyc: KycProfile, *, adjust_checks: Any = None) -> None:
    """Put this KYC on the admin verification desk (or refresh the open request) and alert admins."""
    from datetime import timedelta

    from apps.identity.models import Role, UserStatus
    from apps.kyc.models import VerificationKind, VerificationRequest, VerificationStatus
    from apps.notifications.services import notify

    if user.role == Role.CREATOR:
        from apps.creators.models import Creator

        if not getattr(user, "creator", None) and not Creator.objects.filter(user=user).exists():
            from apps.creators.services import create_creator_for_user

            display_name = user.full_name or (user.email or "").split("@")[0] or "Creator"
            create_creator_for_user(user=user, display_name=display_name)
    elif user.role == Role.BRAND:
        from apps.brands.models import Brand

        if not getattr(user, "brand", None) and not Brand.objects.filter(owner=user).exists():
            from apps.brands.services import create_brand_for_user

            create_brand_for_user(user=user, name=user.full_name or "Brand")

    checks = run_automated_checks(user, kyc)
    if adjust_checks is not None:
        checks = adjust_checks(checks)
    kind = VerificationKind.BRAND if user.role == Role.BRAND else VerificationKind.CREATOR
    hours = 48 if kind == VerificationKind.BRAND else 24
    sla_due_at = timezone.now() + timedelta(hours=hours)

    vr = VerificationRequest.objects.filter(
        user=user,
        kyc_profile=kyc,
        status__in=[VerificationStatus.PENDING, VerificationStatus.INFO_REQUESTED],
    ).first()
    if vr is None:
        vr = VerificationRequest.objects.create(
            user=user,
            kyc_profile=kyc,
            kind=kind,
            status=VerificationStatus.PENDING,
            sla_due_at=sla_due_at,
            submitted_at=timezone.now(),
            checks=checks,
        )
    else:
        vr.kind = kind
        vr.status = VerificationStatus.PENDING
        vr.sla_due_at = sla_due_at
        vr.submitted_at = timezone.now()
        vr.decided_at = None
        vr.decided_by = None
        vr.decision_reason = ""
        vr.decision_note = ""
        vr.requested_items = []
        vr.checks = checks
        vr.save()

    AuditLog.objects.create(actor=user, action="kyc.submitted", entity_type="kyc", entity_id=str(kyc.pk))

    # Notify admins
    admins = User.objects.filter(role=Role.ADMIN, is_staff=True, status=UserStatus.ACTIVE)
    name = user.full_name or user.email or user.phone or "User"
    for adm in admins:
        notify(
            user_id=adm.id,
            type="verification_submitted",
            title="New KYC Verification Submitted",
            body=f"{kind.title()} verification submitted for {name}",
            data={"verificationId": str(vr.id), "kind": kind, "userId": str(user.id)},
        )


def _kind_of(user: User) -> str:
    from apps.identity.models import Role
    from apps.kyc.models import VerificationKind

    return VerificationKind.BRAND if user.role == Role.BRAND else VerificationKind.CREATOR


def approval_side_effects(*, kind: str, user_id: Any, checks: list[dict[str, Any]] | None) -> None:
    """What an approved verification unlocks, for every review path (API desk and Django admin).
    A creator becomes approved (listed). A brand is marked registration-verified / GSTIN-active only when its
    GSTIN was actually checked against the PAN: brand KYC is voluntary and may carry no GSTIN at all."""
    from apps.kyc.models import VerificationKind

    if kind == VerificationKind.CREATOR:
        from apps.creators.models import ApprovalStatus, Creator

        Creator.objects.filter(user_id=user_id).update(approval_status=ApprovalStatus.APPROVED)
    elif kind == VerificationKind.BRAND:
        gstin_checked = any(c.get("key") == "gstin_pan_match" and c.get("result") == "pass" for c in checks or [])
        if gstin_checked:
            from apps.brands.models import Brand

            Brand.objects.filter(owner_id=user_id).update(reg_verified=True, gstin_status="active")


def review(*, actor: User, kyc_id: UUID, approve: bool, reason: str = "") -> KycProfile:
    """Ops decision from the Django admin. Only submitted (pending) profiles can be reviewed."""
    if not approve and not reason.strip():
        raise DomainError("A rejection reason is required.", code="KYC_REASON_REQUIRED")
    with transaction.atomic():
        kyc = KycProfile.objects.select_for_update().get(pk=kyc_id)
        if kyc.status != KycStatus.PENDING:
            raise Conflict("Only submitted KYC can be reviewed.", code="KYC_NOT_PENDING")
        if kyc.user_id == actor.pk:
            raise Forbidden("You can't review your own KYC.", code="KYC_SELF_REVIEW")
        from apps.kyc.models import VerificationRequest, VerificationStatus

        vr = (
            VerificationRequest.objects.select_for_update()
            .filter(kyc_profile=kyc, status__in=[VerificationStatus.PENDING, VerificationStatus.INFO_REQUESTED])
            .first()
        )
        if approve and vr is not None and any(c.get("blocking") is True for c in vr.checks or []):
            raise Conflict("Verification has blocking checks that must be resolved.", code="VERIFICATION_BLOCKED")
        kyc.status = KycStatus.VERIFIED if approve else KycStatus.REJECTED
        kyc.pan_verified = approve and kyc.pan_done
        kyc.bank_verified = approve and kyc.bank_done
        kyc.rejection_reason = "" if approve else reason.strip()[:300]
        kyc.review_message = "" if approve else reason.strip()[:300]
        kyc.reviewed_at = timezone.now()
        kyc.reviewed_by = actor
        kyc.save(
            update_fields=[
                "status",
                "rejection_reason",
                "review_message",
                "reviewed_at",
                "reviewed_by",
                "pan_verified",
                "bank_verified",
                "updated_at",
            ]
        )
        if vr is not None:  # keep the verification desk in step with this decision
            vr.status = VerificationStatus.APPROVED if approve else VerificationStatus.REJECTED
            vr.decided_at = timezone.now()
            vr.decided_by = actor
            vr.decision_reason = kyc.rejection_reason
            vr.save(update_fields=["status", "decided_at", "decided_by", "decision_reason", "updated_at"])
        if approve:
            approval_side_effects(
                kind=vr.kind if vr else _kind_of(kyc.user), user_id=kyc.user_id, checks=vr.checks if vr else []
            )
        AuditLog.objects.create(
            actor=actor, action=f"kyc.{kyc.status}", entity_type="kyc", entity_id=str(kyc.pk), metadata={}
        )
    from apps.notifications.services import notify

    notify(
        user_id=kyc.user_id,
        type=f"kyc_{kyc.status}",
        title="KYC verified" if approve else "KYC needs changes",
        data={"reason": kyc.rejection_reason} if not approve else {},
    )
    return kyc


def summary(kyc: KycProfile | None) -> dict[str, Any]:
    """Masked view for the owner. Never includes the full PAN or account number."""
    k = kyc or KycProfile()
    review_data = None
    if k.status in (KycStatus.REJECTED, KycStatus.ACTION_NEEDED, KycStatus.VERIFIED) or k.reviewed_at:
        review_data = {
            "reason": k.rejection_reason or None,
            "requestedItems": list(k.requested_items or []),
            "message": k.review_message or (k.rejection_reason if k.status == KycStatus.REJECTED else None),
            "decidedAt": k.reviewed_at,
        }
    return {
        "status": k.status,
        "rejectionReason": k.rejection_reason or None,
        "submittedAt": k.submitted_at,
        "steps": {
            "pan": {"done": k.pan_done, "nameOnPan": k.pan_name or None, "panLast4": k.pan_last4 or None},
            "id": {"done": k.id_done, "idType": k.id_type or None},
            "bank": {
                "done": k.bank_done,
                "accountHolderName": k.bank_holder_name or None,
                "accountLast4": k.bank_account_last4 or None,
                "ifsc": k.bank_ifsc or None,
            },
        },
        "review": review_data,
    }
