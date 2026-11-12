from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.exceptions import DomainError
from apps.identity import services
from apps.identity.serializers import (
    AuthTokensOutput,
    GoogleSignInInput,
    LoginInput,
    MeOutput,
    OtpRequestInput,
    OtpRequestOutput,
    OtpVerifyInput,
    PasswordChangeInput,
    PasswordResetInput,
    RefreshInput,
    SignupCompleteInput,
)


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


class GoogleSignInView(APIView):
    """POST {idToken, role?} -> {access, refresh, user}, same shape as OTP verify."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "otp_verify"

    @extend_schema(request=GoogleSignInInput, responses=AuthTokensOutput)
    def post(self, request: Request) -> Response:
        data = GoogleSignInInput(data=request.data)
        data.is_valid(raise_exception=True)
        result = services.google_sign_in(id_token=data.validated_data["idToken"], role=data.validated_data.get("role"))
        return Response(
            AuthTokensOutput({"access": result.access, "refresh": result.refresh, "user": result.user}).data
        )


class LoginView(APIView):
    """POST {identifier, password} -> {access, refresh, user}. `identifier` is an email or mobile number."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "login"

    @extend_schema(request=LoginInput, responses=AuthTokensOutput)
    def post(self, request: Request) -> Response:
        data = LoginInput(data=request.data)
        data.is_valid(raise_exception=True)
        result = services.login_with_password(**data.validated_data, ip=_client_ip(request))
        return Response(
            AuthTokensOutput({"access": result.access, "refresh": result.refresh, "user": result.user}).data
        )


class PasswordChangeView(APIView):
    """POST {currentPassword?, password, confirmPassword} -> {access, refresh, user}.

    `currentPassword` may be left out within 10 minutes of signing in with a code or Google; otherwise
    403 REAUTH_REQUIRED. Signs out every other session; store the returned tokens.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "login"

    @extend_schema(request=PasswordChangeInput, responses=AuthTokensOutput)
    def post(self, request: Request) -> Response:
        data = PasswordChangeInput(data=request.data, context={"user": request.user})
        data.is_valid(raise_exception=True)
        v = data.validated_data
        token = request.auth
        result = services.change_password(
            user=request.user,
            password=v["password"],
            current_password=v.get("currentPassword") or None,
            auth_method=token.get("amr") if token is not None else None,
            auth_time=token.get("auth_time") if token is not None else None,
        )
        return Response(
            AuthTokensOutput({"access": result.access, "refresh": result.refresh, "user": result.user}).data
        )


class PasswordResetView(APIView):
    """POST {challengeId, code, password, confirmPassword} -> {access, refresh, user}.

    Forgot password: get a code from /otp/request/ first. Signs out every other session.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_scope = "otp_verify"

    @extend_schema(request=PasswordResetInput, responses=AuthTokensOutput)
    def post(self, request: Request) -> Response:
        data = PasswordResetInput(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        result = services.reset_password(
            challenge_id=v["challengeId"], code=v["code"], password=v["password"], ip=_client_ip(request)
        )
        return Response(
            AuthTokensOutput({"access": result.access, "refresh": result.refresh, "user": result.user}).data
        )


class TokenRefreshEnvelopeView(TokenRefreshView):
    """POST {refresh} -> {access, refresh}. Refresh tokens rotate; the old one is blacklisted."""

    authentication_classes: list = []
    throttle_scope = "otp_verify"


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


class SignupCompleteView(APIView):
    @extend_schema(request=SignupCompleteInput, responses=MeOutput)
    def post(self, request: Request) -> Response:
        data = SignupCompleteInput(data=request.data, context={"user": request.user})
        data.is_valid(raise_exception=True)
        v = data.validated_data
        user = services.complete_signup(
            user=request.user,
            role=v["role"],
            full_name=v["fullName"],
            brand_name=v.get("brandName", ""),
            display_name=v.get("displayName", ""),
            password=v.get("password") or None,
        )
        return Response(MeOutput(user).data, status=status.HTTP_201_CREATED)
