import logging
from typing import Any

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.exceptions import Conflict, DomainError
from apps.identity import services
from apps.identity.models import User
from apps.identity.serializers import (
    AuthTokensOutput,
    EmailVerificationRequestInput,
    EmailVerificationRequestOutput,
    EmailVerificationVerifyInput,
    EmailVerificationVerifyOutput,
    MeOutput,
    MeUpdateInput,
    OtpRequestInput,
    OtpRequestOutput,
    OtpVerifyInput,
    RefreshInput,
    SignupCompleteInput,
)

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str | None:
    # DRF's throttle uses NUM_PROXIES to compute the real IP; reuse its logic.
    from rest_framework.throttling import BaseThrottle

    return BaseThrottle().get_ident(request) or None


class OtpRequestView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "otp_request"

    @extend_schema(request=OtpRequestInput, responses={202: OtpRequestOutput})
    def post(self, request: Request) -> Response:
        data = OtpRequestInput(data=request.data)
        data.is_valid(raise_exception=True)
        challenge = services.request_otp(**data.validated_data, ip=_client_ip(request))
        # Same response whether or not an account exists (no user enumeration).
        return Response(
            {"challengeId": challenge.id, "expiresIn": settings.OTP_TTL_SECONDS}, status=status.HTTP_202_ACCEPTED
        )


class OtpVerifyView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "otp_verify"

    @extend_schema(request=OtpVerifyInput, responses=AuthTokensOutput)
    def post(self, request: Request) -> Response:
        data = OtpVerifyInput(data=request.data)
        data.is_valid(raise_exception=True)
        result = services.verify_otp(challenge_id=data.validated_data["challengeId"], code=data.validated_data["code"])
        return Response(
            AuthTokensOutput({"access": result.access, "refresh": result.refresh, "user": result.user}).data
        )


class TokenRefreshEnvelopeView(TokenRefreshView):
    """POST {refresh} -> {access, refresh, user}. Refresh tokens rotate; the old one is blacklisted."""

    authentication_classes: list = []
    throttle_scope = "otp_verify"

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200 and isinstance(response.data, dict):
            access_str = response.data.get("access")
            if access_str:
                try:
                    token = AccessToken(access_str)
                    user = User.objects.filter(pk=token.get("sub")).first()
                    if user:
                        response.data["user"] = MeOutput(user).data
                except TokenError as exc:
                    logger.debug("Token decode error on refresh: %s", exc)
        return response


class EmailVerificationRequestView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "otp_request"

    @extend_schema(request=EmailVerificationRequestInput, responses={200: EmailVerificationRequestOutput})
    def post(self, request: Request) -> Response:
        data = EmailVerificationRequestInput(data=request.data)
        data.is_valid(raise_exception=True)
        challenge = services.request_email_verification(
            user=request.user,
            email=data.validated_data["email"],
            ip=_client_ip(request),
        )
        return Response(
            {
                "challengeId": challenge.id,
                "expiresAt": challenge.expires_at.isoformat(),
                "expiresIn": settings.OTP_TTL_SECONDS,
            },
            status=status.HTTP_200_OK,
        )


class EmailVerificationVerifyView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "otp_verify"

    @extend_schema(request=EmailVerificationVerifyInput, responses=EmailVerificationVerifyOutput)
    def post(self, request: Request) -> Response:
        data = EmailVerificationVerifyInput(data=request.data)
        data.is_valid(raise_exception=True)
        user = services.verify_email_challenge(
            user=request.user,
            challenge_id=data.validated_data["challengeId"],
            code=data.validated_data["code"],
        )
        return Response(
            {
                "email": user.email,
                "emailVerified": user.email_verified,
            },
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    @extend_schema(request=RefreshInput, responses={204: None})
    def post(self, request: Request) -> Response:
        data = RefreshInput(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            token = RefreshToken(data.validated_data["refresh"])
        except TokenError:
            raise DomainError("Invalid refresh token.", code="AUTH_INVALID") from None
        if str(token.get("sub")) != str(request.user.pk):
            raise DomainError("Invalid refresh token.", code="AUTH_INVALID")
        token.blacklist()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LogoutAllView(APIView):
    @extend_schema(request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        for outstanding in OutstandingToken.objects.filter(user=request.user):
            BlacklistedToken.objects.get_or_create(token=outstanding)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeOutput)
    def get(self, request: Request) -> Response:
        return Response(MeOutput(request.user).data)

    @extend_schema(request=MeUpdateInput, responses=MeOutput)
    def patch(self, request: Request) -> Response:
        data = MeUpdateInput(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        user = request.user
        update_fields: list[str] = []
        if "email" in v and v["email"] != user.email:
            new_email = v["email"].lower()
            if User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
                raise Conflict("This email is already in use by another account.", code="EMAIL_ALREADY_IN_USE")
            user.email = new_email
            update_fields.append("email")
        if "fullName" in v:
            user.full_name = v["fullName"]
            update_fields.append("full_name")
        if update_fields:
            update_fields.append("updated_at")
            user.save(update_fields=update_fields)
        return Response(MeOutput(user).data)


class SignupCompleteView(APIView):
    @extend_schema(request=SignupCompleteInput, responses=MeOutput)
    def post(self, request: Request) -> Response:
        data = SignupCompleteInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        user = services.complete_signup(
            user=request.user,
            role=v["role"],
            full_name=v["fullName"],
            brand_name=v.get("brandName", ""),
            display_name=v.get("displayName", ""),
        )
        return Response(MeOutput(user).data, status=status.HTTP_201_CREATED)
