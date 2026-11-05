from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.identity.models import Role


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
