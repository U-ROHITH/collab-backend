from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.identity.models import User


class Command(BaseCommand):
    help = "Create a TOTP device for a staff user and print the otpauth:// URL to add to an authenticator app."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument("email")

    def handle(self, *args: object, **opts: str) -> None:
        user = User.objects.filter(email=opts["email"].lower(), is_staff=True).first()
        if not user:
            raise CommandError("No staff user with that email.")
        TOTPDevice.objects.filter(user=user).delete()
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
        self.stdout.write("Add this to your authenticator app (shown once):")
        self.stdout.write(device.config_url)
