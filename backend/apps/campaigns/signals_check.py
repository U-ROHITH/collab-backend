import re
from typing import Any

from apps.campaigns.models import Campaign
from apps.kyc.models import KycStatus

FREEMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
    "zoho.com",
    "rediffmail.com",
    "yopmail.com",
}

PHONE_PATTERN = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}\b")
EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
CONTACT_SOLICITATION = re.compile(
    r"(?i)\b(whatsapp|telegram|signal|insta\s*dm|dm\s*me|call\s*me|phone|contact\s*me|reach\s*out\s*at|wa\.me)\b"
)
PAYMENT_SOLICITATION = re.compile(
    r"(?i)\b(gpay|google\s*pay|phonepe|paytm|upi|cash|direct\s*transfer|bank\s*transfer|neft|rtgs|pay\s*outside)\b"
)


def compute_campaign_signals(campaign: Campaign) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []

    # 1. Brand Verification
    brand = campaign.brand
    is_verified = brand.reg_verified
    if not is_verified and brand.owner:
        owner_kyc = getattr(brand.owner, "kyc", None)
        if owner_kyc and owner_kyc.status == KycStatus.VERIFIED:
            is_verified = True

    if is_verified:
        signals.append(
            {
                "key": "brand_verified",
                "label": "Brand Verification",
                "result": "pass",
                "text": "Brand is verified with valid business registration / KYC",
                "blocking": False,
            }
        )
    else:
        signals.append(
            {
                "key": "brand_verified",
                "label": "Brand Verification",
                "result": "warn",
                "text": "Brand has not completed business registration verification",
                "blocking": False,
            }
        )

    # 2. Business Email Domain
    email = brand.owner.email if brand.owner else ""
    domain = email.split("@")[-1].lower() if "@" in email else ""
    if domain and domain not in FREEMAIL_DOMAINS:
        signals.append(
            {
                "key": "business_email",
                "label": "Business Email Domain",
                "result": "pass",
                "text": f"Registered with business domain (@{domain})",
                "blocking": False,
            }
        )
    else:
        signals.append(
            {
                "key": "business_email",
                "label": "Business Email Domain",
                "result": "warn",
                "text": f"Using generic personal email provider (@{domain or 'unknown'})",
                "blocking": False,
            }
        )

    # 3. Budget Sanity
    min_p = campaign.budget_min_paise
    max_p = campaign.budget_max_paise
    if min_p >= 50_000 and max_p <= 100_000_000 and min_p <= max_p:
        signals.append(
            {
                "key": "budget_sanity",
                "label": "Budget Sanity",
                "result": "pass",
                "text": f"Budget within normal range (₹{min_p // 100:,} - ₹{max_p // 100:,})",
                "blocking": False,
            }
        )
    else:
        signals.append(
            {
                "key": "budget_sanity",
                "label": "Budget Sanity",
                "result": "warn",
                "text": f"Budget outside typical range (₹{min_p // 100:,} - ₹{max_p // 100:,})",
                "blocking": False,
            }
        )

    # 4. Contact & Payment Solicitation
    text_to_check = f"{campaign.title} {campaign.brief}"
    matched: list[str] = []
    if PHONE_PATTERN.search(text_to_check):
        matched.append("phone number")
    if EMAIL_PATTERN.search(text_to_check):
        matched.append("email address")
    if CONTACT_SOLICITATION.search(text_to_check):
        matched.append("direct messaging / external contact")
    if PAYMENT_SOLICITATION.search(text_to_check):
        matched.append("off-platform payment term")

    if matched:
        signals.append(
            {
                "key": "solicitation_check",
                "label": "Off-Platform Solicitation",
                "result": "warn",
                "text": f"Potential off-platform solicitation detected: {', '.join(matched)}",
                "blocking": False,
            }
        )
    else:
        signals.append(
            {
                "key": "solicitation_check",
                "label": "Off-Platform Solicitation",
                "result": "pass",
                "text": "No off-platform contact info or payment solicitation detected",
                "blocking": False,
            }
        )

    return signals
