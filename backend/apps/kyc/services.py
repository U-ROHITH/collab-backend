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


def run_automated_checks(user: User, kyc: KycProfile) -> list[dict[str, Any]]:
    import re

    from apps.identity.models import Role

    checks: list[dict[str, Any]] = []

    def _normalize(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    # 1. Age check (Creator)
    if user.role == Role.CREATOR:
        creator = getattr(user, "creator", None)
        if creator and creator.dob:
            today = timezone.now().date()
            dob = creator.dob
            age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
            if age >= 18:
                checks.append(
                    {
                        "key": "age_18_plus",
                        "label": "Age 18+",
                        "result": "pass",
                        "text": f"Creator is {age} years old (DOB: {dob})",
                        "blocking": False,
                    }
                )
            else:
                checks.append(
                    {
                        "key": "age_18_plus",
                        "label": "Age 18+",
                        "result": "fail",
                        "text": f"Creator is under 18 ({age} years old; DOB: {dob})",
                        "blocking": True,
                    }
                )
        else:
            checks.append(
                {
                    "key": "age_18_plus",
                    "label": "Age 18+",
                    "result": "warn",
                    "text": "Date of birth not provided on creator profile",
                    "blocking": False,
                }
            )

    # 2. PAN vs Profile name comparison
    pan_name = (kyc.pan_name or "").strip()
    profile_name = (user.full_name or "").strip()
    if user.role == Role.BRAND:
        brand = getattr(user, "brand", None)
        if brand and brand.signatory_name:
            profile_name = brand.signatory_name.strip()
        elif brand and brand.legal_name:
            profile_name = brand.legal_name.strip()

    norm_pan = _normalize(pan_name)
    norm_profile = _normalize(profile_name)
    if norm_pan and norm_profile:
        if norm_pan == norm_profile or norm_pan in norm_profile or norm_profile in norm_pan:
            checks.append(
                {
                    "key": "name_match",
                    "label": "PAN vs Profile Name",
                    "result": "pass",
                    "text": f"PAN name '{pan_name}' matches profile name '{profile_name}'",
                    "blocking": False,
                }
            )
        else:
            checks.append(
                {
                    "key": "name_match",
                    "label": "PAN vs Profile Name",
                    "result": "warn",
                    "text": f"PAN name '{pan_name}' differs from profile name '{profile_name}'",
                    "blocking": False,
                }
            )
    elif not norm_pan:
        checks.append(
            {
                "key": "name_match",
                "label": "PAN vs Profile Name",
                "result": "fail",
                "text": "Name on PAN is missing",
                "blocking": True,
            }
        )
    else:
        checks.append(
            {
                "key": "name_match",
                "label": "PAN vs Profile Name",
                "result": "warn",
                "text": "Profile name is not set",
                "blocking": False,
            }
        )

    # 3. PAN inside GSTIN (Brand)
    if user.role == Role.BRAND:
        brand = getattr(user, "brand", None)
        gstin = (brand.gstin if brand else "").strip().upper()
        if gstin and len(gstin) == 15:
            gstin_pan = gstin[2:12]
            actual_pan = ""
            if brand and brand.company_pan:
                actual_pan = brand.company_pan.strip().upper()
            elif kyc.pan_ciphertext:
                try:
                    actual_pan = crypto.decrypt(kyc.pan_ciphertext, context=f"kyc.pan:{user.pk}").strip().upper()
                except Exception:
                    actual_pan = ""
            if actual_pan:
                if gstin_pan == actual_pan:
                    checks.append(
                        {
                            "key": "gstin_pan_match",
                            "label": "GSTIN PAN Match",
                            "result": "pass",
                            "text": f"PAN {actual_pan} matches GSTIN characters 3..12",
                            "blocking": False,
                        }
                    )
                else:
                    checks.append(
                        {
                            "key": "gstin_pan_match",
                            "label": "GSTIN PAN Match",
                            "result": "fail",
                            "text": f"PAN {actual_pan} does not match GSTIN {gstin} (expected {gstin_pan})",
                            "blocking": True,
                        }
                    )
            else:
                checks.append(
                    {
                        "key": "gstin_pan_match",
                        "label": "GSTIN PAN Match",
                        "result": "warn",
                        "text": f"GSTIN provided ({gstin}) but no PAN found to compare",
                        "blocking": False,
                    }
                )
        elif gstin:
            checks.append(
                {
                    "key": "gstin_pan_match",
                    "label": "GSTIN PAN Match",
                    "result": "fail",
                    "text": f"Invalid GSTIN length ({len(gstin)} characters)",
                    "blocking": True,
                }
            )
        else:
            checks.append(
                {
                    "key": "gstin_pan_match",
                    "label": "GSTIN PAN Match",
                    "result": "warn",
                    "text": "No GSTIN provided for brand",
                    "blocking": False,
                }
            )

        # 4. Business Email Domain (Brand)
        consumer_domains = {
            "gmail.com",
            "yahoo.com",
            "hotmail.com",
            "outlook.com",
            "icloud.com",
            "rediffmail.com",
            "aol.com",
            "protonmail.com",
        }
        b_email = ""
        if brand and isinstance(brand.billing_address, dict):
            b_email = brand.billing_address.get("email", "")
        if not b_email:
            b_email = user.email or ""
        b_email = b_email.strip().lower()
        domain = b_email.split("@")[-1] if "@" in b_email else ""
        if domain and domain not in consumer_domains:
            checks.append(
                {
                    "key": "business_email_domain",
                    "label": "Business Email Domain",
                    "result": "pass",
                    "text": f"Business email domain: {domain}",
                    "blocking": False,
                }
            )
        else:
            checks.append(
                {
                    "key": "business_email_domain",
                    "label": "Business Email Domain",
                    "result": "warn",
                    "text": f"Consumer email domain: {domain or 'none'}",
                    "blocking": False,
                }
            )

    # 5. Bank Account Holder Name Match
    bank_holder = (kyc.bank_holder_name or "").strip()
    norm_bank = _normalize(bank_holder)
    if norm_bank and norm_pan:
        if norm_bank == norm_pan or norm_bank in norm_pan or norm_pan in norm_bank:
            checks.append(
                {
                    "key": "bank_name_match",
                    "label": "Bank Holder Name Match",
                    "result": "pass",
                    "text": f"Bank holder name '{bank_holder}' matches PAN name '{pan_name}'",
                    "blocking": False,
                }
            )
        else:
            checks.append(
                {
                    "key": "bank_name_match",
                    "label": "Bank Holder Name Match",
                    "result": "warn",
                    "text": f"Bank holder name '{bank_holder}' differs from PAN name '{pan_name}'",
                    "blocking": False,
                }
            )
    elif not norm_bank:
        checks.append(
            {
                "key": "bank_name_match",
                "label": "Bank Holder Name Match",
                "result": "fail",
                "text": "Bank account holder name missing",
                "blocking": True,
            }
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
