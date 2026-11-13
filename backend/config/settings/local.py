from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
DEV_ENDPOINTS_ENABLED = DEBUG and env.bool("DEV_ENDPOINTS_ENABLED", default=False)
ALLOWED_HOSTS = ["*"]

# In local development, disable 2FA requirement for Django Admin so login only needs email & password
ADMIN_REQUIRE_2FA = env.bool("ADMIN_REQUIRE_2FA", default=False)
