from typing import Any

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.identity import services
from apps.identity.serializers import (
    AdminAuthTokensOutput,
    AdminLoginInput,
    AdminLoginOutput,
    AdminLogoutInput,
    AdminMfaVerifyInput,
    AdminUserOutput,
)


def _client_ip(request: Request) -> str | None:
    return BaseThrottle().get_ident(request) or None


@extend_schema(tags=["Admin - Auth"])
class AdminLoginView(APIView):
    """POST {email, password} -> {mfaRequired: true, mfaToken, user}.
    Restricted to admin staff accounts; non-admins receive 403 FORBIDDEN."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_scope = "login"

    @extend_schema(request=AdminLoginInput, responses=AdminLoginOutput)
    def post(self, request: Request) -> Response:
        data = AdminLoginInput(data=request.data)
        data.is_valid(raise_exception=True)
        res = services.login_admin(**data.validated_data, ip=_client_ip(request))
        return Response(
            {
                "mfaRequired": res["mfaRequired"],
                "mfaToken": res["mfaToken"],
                "user": AdminUserOutput(res["user"]).data,
            }
        )


@extend_schema(tags=["Admin - Auth"])
class AdminMfaVerifyView(APIView):
    """POST {mfaToken, code} (or {email, password, code}) -> {access, refresh, user}."""

    authentication_classes: list[Any] = []
    permission_classes = [AllowAny]
    throttle_scope = "login"

    @extend_schema(request=AdminMfaVerifyInput, responses=AdminAuthTokensOutput)
    def post(self, request: Request) -> Response:
        data = AdminMfaVerifyInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        result = services.verify_admin_mfa(
            code=v["code"],
            mfa_token=v.get("mfaToken"),
            email=v.get("email"),
            password=v.get("password"),
            ip=_client_ip(request),
        )
        return Response(
            {
                "access": result.access,
                "refresh": result.refresh,
                "user": AdminUserOutput(result.user).data,
            }
        )


@extend_schema(tags=["Admin - Auth"])
class AdminLogoutView(APIView):
    """POST {refresh?} -> signs out admin, blacklists token, logs audit event."""

    permission_classes = [IsAdminUser]

    @extend_schema(request=AdminLogoutInput, responses={200: None})
    def post(self, request: Request) -> Response:
        data = AdminLogoutInput(data=request.data)
        data.is_valid(raise_exception=True)
        refresh = data.validated_data.get("refresh")
        if refresh:
            try:
                token = RefreshToken(refresh)
                token.blacklist()
            except Exception:  # noqa: S110
                pass
        services.record_audit(admin=request.user, action="admin.logout", target=request.user, detail={})
        return Response({"success": True, "message": "Successfully logged out."}, status=status.HTTP_200_OK)
