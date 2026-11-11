from datetime import timedelta

from .base import *  # noqa: F403
from .base import SIMPLE_JWT, env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
DEV_ENDPOINTS_ENABLED = DEBUG and env.bool("DEV_ENDPOINTS_ENABLED", default=False)
ALLOWED_HOSTS = ["*"]

# In local development, default OTP delivery to console so SMS/email signups work without external gateways
OTP_DELIVERY = env("OTP_DELIVERY", default="console")

# Keep tokens valid for 7 days in local development so Postman/Swagger sessions don't expire
SIMPLE_JWT = {
    **SIMPLE_JWT,
    "ACCESS_TOKEN_LIFETIME": timedelta(days=7),
}
