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


class FakeStorage:
    """In-memory stand-in for Supabase Storage (tests never touch the network)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None:  # what the browser would do
        self.objects[(bucket, path)] = (data, content_type)

    def install(self, monkeypatch: Any) -> None:
        from apps.uploads import storage as s

        monkeypatch.setattr(s, "create_signed_upload_url", lambda b, p: f"https://storage.test/sign/{b}/{p}?token=t")
        monkeypatch.setattr(
            s,
            "head",
            lambda b, p: (
                {"content_type": self.objects[(b, p)][1], "size": len(self.objects[(b, p)][0])}
                if (b, p) in self.objects
                else None
            ),
        )
        monkeypatch.setattr(s, "read_prefix", lambda b, p, n=64: self.objects[(b, p)][0][:n])
        monkeypatch.setattr(s, "download", lambda b, p, max_bytes: self.objects[(b, p)][0])
        monkeypatch.setattr(s, "upload", lambda b, p, d, ct: self.objects.__setitem__((b, p), (d, ct)))
        monkeypatch.setattr(s, "delete", lambda b, ps: [self.objects.pop((b, p), None) for p in ps])
        monkeypatch.setattr(s, "signed_download_url", lambda b, p, e: f"https://storage.test/signed/{b}/{p}?exp={e}")


@pytest.fixture
def fake_storage(monkeypatch: pytest.MonkeyPatch) -> FakeStorage:
    fs = FakeStorage()
    fs.install(monkeypatch)
    return fs
