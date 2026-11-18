import os
import subprocess
import sys
from pathlib import Path

import pytest

PROBE = """
import sys
from django.core.exceptions import ImproperlyConfigured

try:
    import config.settings.production
except ImproperlyConfigured as exc:
    print("IMPROPERLY_CONFIGURED:", str(exc))
    sys.exit(0)

print("PRODUCTION_SETTINGS_ACCEPTED")
"""

EXPECTED_GUARD_MESSAGE = (
    "OTP_TEST_ACCOUNTS and OTP_TEST_CODE must be empty in production."
)

BASE_ENVIRONMENT = {
    "DJANGO_SETTINGS_MODULE": "config.settings.production",
    "DJANGO_SECRET_KEY": "test-only-secret-key-that-is-longer-than-fifty-characters",
    "DJANGO_ALLOWED_HOSTS": "example.com",
    "DATABASE_URL": "postgres://test:test@127.0.0.1:5432/test",
    "JWT_SIGNING_KEY": "test-only-jwt-signing-key",
    "DATA_ENCRYPTION_KEY": "test-only-data-encryption-key-long-enough",
    "OTP_DELIVERY": "live",
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-only-placeholder",
    "TWO_FACTOR_API_KEY": "test-only-placeholder",
    "RESEND_API_KEY": "test-only-placeholder",
    "RAZORPAY_ENABLED": "False",
}


@pytest.mark.parametrize(
    ("accounts", "code", "expected_output"),
    [
        ("+919876543210", "", EXPECTED_GUARD_MESSAGE),
        ("", "123456", EXPECTED_GUARD_MESSAGE),
        ("", "", "PRODUCTION_SETTINGS_ACCEPTED"),
    ],
    ids=[
        "reject-test-accounts",
        "reject-fixed-otp-code",
        "accept-empty-test-settings",
    ],
)
def test_production_settings_otp_guard(
    accounts: str,
    code: str,
    expected_output: str,
) -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.update(BASE_ENVIRONMENT)
    environment["OTP_TEST_ACCOUNTS"] = accounts
    environment["OTP_TEST_CODE"] = code
    environment["PYTHONPATH"] = str(backend_dir)

    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=backend_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert expected_output in output, output