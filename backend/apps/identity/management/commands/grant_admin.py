import getpass

from django.core.management.base import BaseCommand, CommandError

from apps.identity.services import grant_admin


class Command(BaseCommand):
    help = "Create or promote a staff admin (the only way to get the admin role). Prompts for password."

    def add_arguments(self, parser) -> None:  # type: ignore[no-untyped-def]
        parser.add_argument("email")

    def handle(self, *args: object, **opts: str) -> None:
        password = getpass.getpass("Password (min 12 chars): ")
        if len(password) < 12:
            raise CommandError("Password must be at least 12 characters.")
        user = grant_admin(email=opts["email"], password=password)
        self.stdout.write(self.style.SUCCESS(f"Admin granted: {user.email}. Now run: manage.py add_totp {user.email}"))
