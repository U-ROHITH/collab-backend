from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
DEV_ENDPOINTS_ENABLED = DEBUG and env.bool("DEV_ENDPOINTS_ENABLED", default=False)
