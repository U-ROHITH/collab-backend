"""Base settings shared by every environment. Secrets come only from env (backend/.env locally)."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["*"])
# Render sets RENDER_EXTERNAL_HOSTNAME (e.g. collab-api-ewju.onrender.com); always allow it.
if (_render_hostname := env("RENDER_EXTERNAL_HOSTNAME", default="")) and "*" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_hostname)
ADMIN_URL = env("DJANGO_ADMIN_URL", default="ops/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_spectacular",
    "channels",
    "apps.core",
    "apps.identity",
    "apps.brands",
    "apps.creators",
    "apps.campaigns",
    "apps.offers",
    "apps.orders",
    "apps.notifications",
    "apps.uploads",
    "apps.messaging",
    "apps.kyc",
    "apps.realtime",
]

MIDDLEWARE = [
    "apps.core.middleware.HealthCheckMiddleware",  # first: before host validation and the HTTPS redirect
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "apps.core.middleware.RequestIdMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.core.middleware.ApiOptionalSlashMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

# --- Database -------------------------------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 0
# Supabase transaction pooler does not support server-side cursors.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
DATABASES["default"].setdefault("OPTIONS", {})
ATOMIC_REQUESTS = False  # transactions are explicit in services
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "identity.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Admin login (session + CSRF) needs the public origin when behind Render's proxy.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if _render_host := env("RENDER_EXTERNAL_HOSTNAME", default=""):
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

# Uploads never stream through the API (presigned uploads); keep request bodies small.
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

# --- Cache (throttles) ----------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="")
if REDIS_URL:
    # Shared, bounded pool + fail-open; see apps/core/cache.py for why the stock backend broke under ASGI.
    CACHES = {"default": {"BACKEND": "apps.core.cache.ResilientRedisCache", "LOCATION": REDIS_URL}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# --- Realtime (Django Channels) -----------------------------------------------------------------
# Redis is required when more than one process serves WebSockets (production); in-memory is dev-only.
if REDIS_URL:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {"hosts": [REDIS_URL]}}}
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# --- File storage (Supabase Storage; service key is server-only) -----------------------------
SUPABASE_URL = env("SUPABASE_URL", default="")
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", default="")
STORAGE_PUBLIC_BUCKET = env("STORAGE_PUBLIC_BUCKET", default="public-media")
STORAGE_PRIVATE_BUCKET = env("STORAGE_PRIVATE_BUCKET", default="private")
GALLERY_MAX_ITEMS = 12

# --- CORS -----------------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
# Patterns for preview deployments, e.g. ^https://linkloom-frontend-[a-z0-9-]+\.vercel\.app$
CORS_ALLOWED_ORIGIN_REGEXES = env.list("CORS_ALLOWED_ORIGIN_REGEXES", default=[])
CORS_ALLOW_CREDENTIALS = False  # bearer tokens, not cookies
# Off by default. Auth is a bearer header (never cookies), so even allow-all wouldn't let another site act as a
# user, but production lists the frontend origins explicitly.
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)

# --- DRF ------------------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.identity.authentication.ActiveUserJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["apps.core.renderers.EnvelopeJSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.envelope_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.EnvelopeCursorPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "120/min",
        "otp_request": "5/min",
        "otp_verify": "10/min",
        "login": "10/min",
        "bids": "30/hour",
        "offers": "50/day",
        "writes": "60/min",
        "messages": "30/min",
    },
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    "UNAUTHENTICATED_USER": None,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY"),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "sub",
    "ISSUER": "collab-api",
    "AUDIENCE": "collab-apps",
    "LEEWAY": 10,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Collab API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "CAMELIZE_NAMES": False,
    "ENUM_NAME_OVERRIDES": {"PlatformEnum": "apps.creators.models.Platform"},
}

# --- KYC / field encryption -----------------------------------------------------------------------
DATA_ENCRYPTION_KEY = env("DATA_ENCRYPTION_KEY", default="")
DATA_ENCRYPTION_KEY_VERSION = env.int("DATA_ENCRYPTION_KEY_VERSION", default=1)
# Kill switch: when False, bids/offers/checkout/campaign creation no longer require verified KYC.
KYC_ENFORCED = env.bool("KYC_ENFORCED", default=True)
# Payouts (Razorpay Route) aren't built. Stays False so the app hides withdraw/payout screens.
PAYOUTS_ENABLED = False

# --- Google sign-in -------------------------------------------------------------------------------
# OAuth client IDs are public (they ship in the frontend bundle), so a default is fine. Comma-separate
# to accept tokens minted for several clients (web, Android, iOS).
GOOGLE_OAUTH_CLIENT_IDS = env.list(
    "GOOGLE_OAUTH_CLIENT_IDS",
    default=["1042168589486-k7e30v1tao2ooepsb5lp339m7anlvq44.apps.googleusercontent.com"],
)

# --- OTP / messaging providers ----------------------------------------------------------------
OTP_DELIVERY = env("OTP_DELIVERY", default="live")  # "live" | "console" (console only allowed with DEBUG)
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
OTP_MAX_PER_DESTINATION_PER_HOUR = 5
# Test logins (QA, app-store review): these destinations get OTP_TEST_CODE instead of a real SMS/email
# and skip the hourly limit. Normalized form: +91XXXXXXXXXX or lower-case email. Empty both before launch.
OTP_TEST_ACCOUNTS = env.list("OTP_TEST_ACCOUNTS", default=[])
OTP_TEST_CODE = env("OTP_TEST_CODE", default="")
TWO_FACTOR_API_KEY = env("TWO_FACTOR_API_KEY", default="")
RESEND_API_KEY = env("RESEND_API_KEY", default="")
RESEND_FROM_EMAIL = env("RESEND_FROM_EMAIL", default="")

EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="")
# Fail fast: some hosts (e.g. Render free) block outbound SMTP, which would otherwise hang the request.
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# --- Money (pending CA sign-off; see docs/04) -------------------------------------------------
FEE_CONFIG = {
    "version": env("FEE_CONFIG_VERSION", default="2026-11-v0"),
    "commission_bps": env.int("COMMISSION_BPS", default=1000),  # 10%
    "gst_bps": env.int("GST_BPS", default=1800),  # 18% on platform fee
    "tds_bps": env.int("TDS_BPS", default=0),  # TBD by CA (194-O vs 194J)
    "tds_section": env("TDS_SECTION", default="TBD"),
    "commission_bearer": env("COMMISSION_BEARER", default="brand"),  # "brand" | "creator"
}

ORDER_POLICY = {
    "max_revisions": 2,
    "payment_window_hours": 48,
    "auto_approve_hours": 72,
}

DEV_ENDPOINTS_ENABLED = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {"urllib3": {"level": "WARNING"}},
}
