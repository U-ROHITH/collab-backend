from django.conf import settings
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django_otp.admin import OTPAdminSite

from apps.core.exceptions import DomainError
from apps.identity import services
from apps.identity.models import AuditLog, User

# Staff must pass TOTP 2FA to use the Django admin (create a device with `manage.py add_totp`).
if getattr(settings, "ADMIN_REQUIRE_2FA", True):
    admin.site.__class__ = OTPAdminSite
admin.site.site_header = "Collab Ops"


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "phone", "role", "admin_role", "status", "is_staff", "created_at")
    list_filter = ("role", "admin_role", "status", "is_staff")
    search_fields = ("email", "phone", "full_name")
    readonly_fields = ("id", "password", "last_login", "created_at", "updated_at", "role", "is_superuser")
    exclude = ("user_permissions",)
    actions = ["delete_completely"]

    def delete_model(self, request: HttpRequest, obj: User) -> None:
        self._erase(request, [obj])

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        self._erase(request, list(queryset))

    @admin.action(description="Erase selected users (refused for users with orders or payments)")
    def delete_completely(self, request: HttpRequest, queryset: QuerySet[User]) -> None:
        self._erase(request, list(queryset))

    def _erase(self, request: HttpRequest, users: list[User]) -> None:
        erased = 0
        for user in users:
            try:
                services.delete_user_completely(user=user)
                erased += 1
            except DomainError as exc:
                self.message_user(request, f"{user}: {exc.message}", messages.ERROR)
        if erased:
            self.message_user(request, f"Erased {erased} user(s).", messages.SUCCESS)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "entity_type", "entity_id", "hash")
    list_filter = ("action", "entity_type")

    def has_add_permission(self, request: object) -> bool:
        return False

    def has_change_permission(self, request: object, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: object, obj: object = None) -> bool:
        return False
