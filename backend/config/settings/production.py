import re

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, CORS_ALLOWED_ORIGINS, OTP_DELIVERY, OTP_TEST_ACCOUNTS, OTP_TEST_CODE, SECRET_KEY, env

DEBUG = False
DEV_ENDPOINTS_ENABLED = False

if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be at least 50 chars in production.")
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must be set in production.")
if any("*" in o or o.startswith("http://") for o in CORS_ALLOWED_ORIGINS):
    raise ImproperlyConfigured("CORS origins must be exact https origins in production.")
if OTP_DELIVERY != "live":
    raise ImproperlyConfigured("OTP_DELIVERY must be 'live' in production.")
if len(env("DATA_ENCRYPTION_KEY", default="")) < 32:
    raise ImproperlyConfigured("DATA_ENCRYPTION_KEY must be at least 32 chars in production.")
if OTP_TEST_ACCOUNTS and not re.fullmatch(r"\d{6}", OTP_TEST_CODE):
    raise ImproperlyConfigured("OTP_TEST_CODE must be 6 digits when OTP_TEST_ACCOUNTS is set.")
for required in (
    "JWT_SIGNING_KEY",
    "DATA_ENCRYPTION_KEY",
    "TWO_FACTOR_API_KEY",
    "RESEND_API_KEY",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
):
    if not env(required, default=""):
        raise ImproperlyConfigured(f"{required} is required in production.")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^healthz$", r"^readyz$"]  # platform health checks use plain HTTP
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 8 * 3600
CSRF_COOKIE_SECURE = True
