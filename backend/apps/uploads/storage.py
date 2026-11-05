"""Thin Supabase Storage client (service role, server-side only).

The service role key never leaves the server. Browsers only ever receive single-use signed
upload URLs or short-lived signed download URLs.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)
_TIMEOUT = 20


class StorageError(Exception):
    pass


def _base() -> str:
    return f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1"


def _headers() -> dict[str, str]:
    key = settings.SUPABASE_SERVICE_ROLE_KEY
    return {"Authorization": f"Bearer {key}", "apikey": key}


def _check(resp: requests.Response, action: str) -> requests.Response:
    if resp.status_code >= 300:
        logger.error("Storage %s failed with HTTP %s", action, resp.status_code)
        raise StorageError(action)
    return resp


def create_signed_upload_url(bucket: str, path: str) -> str:
    """Single-use URL the browser PUTs the file to. Bucket rules enforce size and MIME type."""
    resp = _check(
        requests.post(f"{_base()}/object/upload/sign/{bucket}/{path}", headers=_headers(), timeout=_TIMEOUT),
        "sign_upload",
    )
    return f"{_base()}{resp.json()['url']}"


def head(bucket: str, path: str) -> dict[str, str | int] | None:
    resp = requests.head(f"{_base()}/object/authenticated/{bucket}/{path}", headers=_headers(), timeout=_TIMEOUT)
    if resp.status_code in (400, 404):
        return None
    _check(resp, "head")
    return {
        "content_type": resp.headers.get("content-type", ""),
        "size": int(resp.headers.get("content-length", "0")),
    }


def read_prefix(bucket: str, path: str, n_bytes: int = 64) -> bytes:
    resp = requests.get(
        f"{_base()}/object/authenticated/{bucket}/{path}",
        headers={**_headers(), "Range": f"bytes=0-{n_bytes - 1}"},
        timeout=_TIMEOUT,
    )
    return _check(resp, "read_prefix").content[:n_bytes]


def download(bucket: str, path: str, max_bytes: int) -> bytes:
    with requests.get(
        f"{_base()}/object/authenticated/{bucket}/{path}", headers=_headers(), timeout=_TIMEOUT, stream=True
    ) as resp:
        _check(resp, "download")
        chunks, total = [], 0
        for chunk in resp.iter_content(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise StorageError("too_large")
            chunks.append(chunk)
    return b"".join(chunks)


def upload(bucket: str, path: str, data: bytes, content_type: str) -> None:
    _check(
        requests.post(
            f"{_base()}/object/{bucket}/{path}",
            headers={**_headers(), "Content-Type": content_type, "x-upsert": "false"},
            data=data,
            timeout=_TIMEOUT,
        ),
        "upload",
    )


def delete(bucket: str, paths: list[str]) -> None:
    if not paths:
        return
    _check(
        requests.delete(f"{_base()}/object/{bucket}", headers=_headers(), json={"prefixes": paths}, timeout=_TIMEOUT),
        "delete",
    )


def signed_download_url(bucket: str, path: str, expires_in: int) -> str:
    resp = _check(
        requests.post(
            f"{_base()}/object/sign/{bucket}/{path}",
            headers=_headers(),
            json={"expiresIn": expires_in},
            timeout=_TIMEOUT,
        ),
        "sign_download",
    )
    return f"{_base()}{resp.json()['signedURL']}"


def public_url(bucket: str, path: str) -> str:
    return f"{_base()}/object/public/{bucket}/{path}"
