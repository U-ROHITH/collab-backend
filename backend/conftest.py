from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.creators.models import ApprovalStatus, CreatorPackage
from apps.identity.models import User
from apps.identity.services import complete_signup, issue_tokens
from tests.fixtures import USAGE_RIGHTS


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def anon() -> APIClient:
    return APIClient()


def _client_for(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_tokens(user).access}")
    client.user = user  # type: ignore[attr-defined]
    return client


@pytest.fixture
def make_brand(db: Any) -> Callable[..., APIClient]:
    counter = iter(range(1000))

    def _make(name: str = "Nimbu Foods") -> APIClient:
        n = next(counter)
        user = User.objects.create_user(email=f"brand{n}-{name.replace(' ', '').lower()}@example.com")
        complete_signup(user=user, role="brand", full_name="Brand Owner", brand_name=name)
        return _client_for(User.objects.get(pk=user.pk))

    return _make


@pytest.fixture
def make_creator(db: Any) -> Callable[..., APIClient]:
    counter = iter(range(1000))

    def _make(name: str = "Ananya Rao", approved: bool = True) -> APIClient:
        n = next(counter)
        user = User.objects.create_user(phone=f"+9198765{n:05d}")
        complete_signup(user=user, role="creator", full_name=name, display_name=name)
        user = User.objects.get(pk=user.pk)
        if approved:
            user.creator.approval_status = ApprovalStatus.APPROVED
            user.creator.save()
        return _client_for(user)

    return _make


@pytest.fixture
def brand(make_brand: Callable[..., APIClient]) -> APIClient:
    return make_brand()


@pytest.fixture
def creator(make_creator: Callable[..., APIClient]) -> APIClient:
    return make_creator()


@pytest.fixture
def package(creator: APIClient) -> CreatorPackage:
    return CreatorPackage.objects.create(
        creator=creator.user.creator,
        title="1 Reel",
        platform="instagram",
        deliverables="1 Reel",
        price_paise=1_000_000,
        delivery_days=5,
    )


@pytest.fixture
def campaign_payload() -> dict[str, Any]:
    return {
        "title": "Festive snack hamper",
        "brief": "Showcase our festive hamper in a fun Reel.",
        "platform": "instagram",
        "deliverables": "1 Reel",
        "budgetMin": 500_000,
        "budgetMax": 1_500_000,
        "closesAt": (timezone.now() + timedelta(days=7)).isoformat(),
        "usageRights": USAGE_RIGHTS,
    }
