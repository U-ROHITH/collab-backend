"""Brand KYC: company PAN + bank account. Voluntary (never gates a brand action); used for refunds and compliance.

Saving either step sends it to the admin verification desk straight away. `panVerified` / `bankVerified` turn true
only when an admin approves, and a changed detail loses its flag and goes back for review.
"""

from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core import crypto
from apps.identity.models import User
from apps.kyc.models import KycProfile, KycStatus
from apps.kyc.services import open_verification

_WIRE_STATUS = {
    KycStatus.NOT_STARTED: "pending",
    KycStatus.IN_PROGRESS: "submitted",
    KycStatus.PENDING: "submitted",
    KycStatus.ACTION_NEEDED: "rejected",
    KycStatus.REJECTED: "rejected",
    KycStatus.VERIFIED: "verified",
}
# A brand may submit PAN or bank alone, so "this step isn't given yet" is expected, not a blocker. Real
# mismatches (e.g. the PAN inside the GSTIN) still block approval.
_MISSING_STEP_TEXT = ("Name on PAN is missing", "Bank account holder name missing")


def status_payload(user: User) -> dict[str, Any]:
    kyc = KycProfile.objects.filter(user=user).first()
    if kyc is None:
        return {"status": "pending", "panVerified": False, "bankVerified": False, "rejectionReason": ""}
    rejected = _WIRE_STATUS[kyc.status] == "rejected"
    return {
        "status": _WIRE_STATUS[kyc.status],
        "panVerified": kyc.pan_verified,
        "bankVerified": kyc.bank_verified,
        "rejectionReason": (kyc.rejection_reason or kyc.review_message or "") if rejected else "",
    }


def _relax_missing_steps(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for check in checks:
        if check.get("result") == "fail" and check.get("text") in _MISSING_STEP_TEXT:
            check = {**check, "result": "warn", "blocking": False}
        out.append(check)
    return out


def _submit(user: User, kyc: KycProfile, fields: list[str]) -> None:
    kyc.status = KycStatus.PENDING
    kyc.submitted_at = timezone.now()
    kyc.rejection_reason = ""
    kyc.review_message = ""
    kyc.save(update_fields=[*fields, "status", "submitted_at", "rejection_reason", "review_message", "updated_at"])
    open_verification(user, kyc, adjust_checks=_relax_missing_steps)


def save_pan(*, user: User, pan: str) -> dict[str, Any]:
    with transaction.atomic():
        kyc, _ = KycProfile.objects.select_for_update().get_or_create(user=user)
        kyc.pan_last4 = pan[-4:]
        kyc.pan_ciphertext = crypto.encrypt(pan, context=f"kyc.pan:{user.pk}")
        kyc.pan_verified = False
        _submit(user, kyc, ["pan_last4", "pan_ciphertext", "pan_verified"])
    return status_payload(user)


def save_bank(*, user: User, account_number: str, ifsc: str, account_name: str) -> dict[str, Any]:
    with transaction.atomic():
        kyc, _ = KycProfile.objects.select_for_update().get_or_create(user=user)
        kyc.bank_holder_name = account_name
        kyc.bank_account_last4 = account_number[-4:]
        kyc.bank_account_ciphertext = crypto.encrypt(account_number, context=f"kyc.bank:{user.pk}")
        kyc.bank_ifsc = ifsc
        kyc.bank_verified = False
        _submit(
            user,
            kyc,
            ["bank_holder_name", "bank_account_last4", "bank_account_ciphertext", "bank_ifsc", "bank_verified"],
        )
    return status_payload(user)


def mark_reviewed(kyc: KycProfile, *, approved: bool) -> None:
    """Called wherever an admin decides: approval verifies the fields that were submitted; rejection clears both."""
    kyc.pan_verified = approved and kyc.pan_done
    kyc.bank_verified = approved and kyc.bank_done
    kyc.save(update_fields=["pan_verified", "bank_verified", "updated_at"])
