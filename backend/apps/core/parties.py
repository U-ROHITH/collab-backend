"""Public profile cards shown to the other side of a deal (orders, offers, chats, bid lists).

Profile details only. Phone numbers, emails, GSTIN, billing address and payout details are
never included here (contact stays on-platform; see docs/03-security.md).
"""

from typing import Any

from apps.creators.models import replies_within_label


def creator_public(c: Any) -> dict[str, Any]:
    follower_count = getattr(c, "follower_count", None)
    if follower_count is None and isinstance(getattr(c, "platform_stats", None), dict):
        primary_stats = c.platform_stats.get(getattr(c, "primary_platform", "instagram"))
        if isinstance(primary_stats, dict) and primary_stats.get("followersCount") is not None:
            follower_count = primary_stats["followersCount"]
        else:
            for p in ("instagram", "youtube"):
                st = c.platform_stats.get(p)
                if isinstance(st, dict) and st.get("followersCount") is not None:
                    follower_count = st["followersCount"]
                    break

    return {
        "id": str(c.id),
        "slug": c.slug,
        "displayName": c.display_name,
        "avatarUrl": c.avatar_url or None,
        "bio": c.bio,
        "categories": list(c.categories or []),
        "location": c.location,
        "primaryPlatform": c.primary_platform,
        "repliesWithin": replies_within_label(c.replies_within),
        "verified": c.verified,
        "followerCount": follower_count,
        "rating": c.rating,
        "reviewCount": c.rating_count,
    }


def brand_public(b: Any) -> dict[str, Any]:
    city = (b.billing_address or {}).get("city", "") if isinstance(b.billing_address, dict) else ""
    return {
        "id": str(b.id),
        "name": b.name,
        "logoUrl": b.logo_url or None,
        "industry": b.industry,
        "website": b.website or None,
        "city": city,
    }
