import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
# Django Channels (messaging) will wrap this in a ProtocolTypeRouter in Phase 3.
application = get_asgi_application()
