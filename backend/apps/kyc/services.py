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

_EDITABLE = (KycStatus.NOT_STARTED, KycStatus.IN_PROGRESS, KycStatus.REJECTED)


class KycRequired(Forbidden):
    code = "KYC_REQUIRED"
    message = "Complete KYC verification to do this."


def status_of(user: User | None) -> str:
    kyc = getattr(user, "kyc", None) if user else None
    return kyc.status if kyc else KycStatus.NOT_STARTED


def require_verified(user: User) -> None:
    """Gate for money-moving actions. Raises 403 KYC_REQUIRED unless verified (or enforcement is off)."""
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
    for party in (brand_owner, creator_user):
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
        kyc.save(update_fields=["status", "submitted_at", "rejection_reason", "updated_at"])
        AuditLog.objects.create(actor=user, action="kyc.submitted", entity_type="kyc", entity_id=str(kyc.pk))
    return kyc


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
        kyc.rejection_reason = "" if approve else reason.strip()[:300]
        kyc.reviewed_at = timezone.now()
        kyc.reviewed_by = actor
        kyc.save(update_fields=["status", "rejection_reason", "reviewed_at", "reviewed_by", "updated_at"])
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
    }
