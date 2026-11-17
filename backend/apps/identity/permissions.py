from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.identity.models import AdminRole, Role


class _HasRole(BasePermission):
    role: str = ""
    message = "This action is not available for your account type."

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role == self.role)


class IsBrand(_HasRole):
    role = Role.BRAND


class IsCreator(_HasRole):
    role = Role.CREATOR


class IsBrandOrCreator(BasePermission):
    message = "Complete signup to use this feature."

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role in (Role.BRAND, Role.CREATOR))


class IsApprovedCreator(IsCreator):
    message = "Your creator profile must be approved first."

    def has_permission(self, request: Request, view: Any) -> bool:
        if not super().has_permission(request, view):
            return False
        creator = getattr(request.user, "creator", None)
        return bool(creator and creator.is_approved)


class IsAdminUser(BasePermission):
    """Requires the user to be authenticated, have role=ADMIN, and is_staff=True.
    Returns 403 FORBIDDEN for non-admins.
    Optionally checks for specific allowed admin_roles."""

    message = "Administrative access required."
    code = "FORBIDDEN"

    def __init__(self, *roles: str) -> None:
        self.allowed_roles = roles

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False

        if user.role != Role.ADMIN or not user.is_staff:
            return False

        # Role check if specific roles are required (either on permission or view)
        roles = self.allowed_roles or getattr(view, "required_admin_roles", None)
        if roles:
            # super_admin always satisfies any admin role requirement
            if getattr(user, "admin_role", None) == AdminRole.SUPER_ADMIN:
                return True
            return getattr(user, "admin_role", None) in roles

        return True


def RequireAdminRoles(*roles: str) -> type[IsAdminUser]:
    """Helper factory for requiring specific admin roles."""

    class _ConfiguredAdminRole(IsAdminUser):
        def __init__(self) -> None:
            super().__init__(*roles)

    return _ConfiguredAdminRole

