import secrets
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.campaigns.models import Bid, BidStatus, Campaign, ModerationStatus
from apps.core.audit import record_audit
from apps.core.exceptions import Conflict, NotFound
from apps.identity.admin_account_serializers import (
    AdminAccountActionResult,
    AdminAccountUserOutput,
    AdminPasswordResetResult,
    AdminReactivateAccountInput,
    AdminSessionOutput,
    AdminSessionsRevokeResult,
    AdminSuspendAccountInput,
)
from apps.identity.models import (
    AdminRole,
    OtpChallenge,
    OtpChannel,
    OtpPurpose,
    Role,
    User,
    UserStatus,
)
from apps.identity.permissions import RequireAdminRoles
from apps.identity.services import _hash_code
from apps.notifications.services import notify


def _resolve_user_id(user_id: UUID) -> UUID:
    """Resolve user_id from either a direct User ID, a Creator ID, or a Brand ID."""
    if User.objects.filter(pk=user_id).exists():
        return user_id
    from apps.creators.models import Creator
    creator_user_id = Creator.objects.filter(pk=user_id).values_list("user_id", flat=True).first()
    if creator_user_id:
        return creator_user_id
    from apps.brands.models import Brand
    brand_user_id = Brand.objects.filter(pk=user_id).values_list("owner_id", flat=True).first()
    if brand_user_id:
        return brand_user_id
    return user_id


@extend_schema(tags=["Admin - Users"])
class AdminSuspendAccountView(APIView):
    """Suspend a user account, revoke sessions, clear featured status if creator, and hide live campaigns if brand."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminSuspendAccountInput, responses={200: AdminAccountActionResult})
    def post(self, request: Request, user_id: UUID) -> Response:
        user_id = _resolve_user_id(user_id)
        input_serializer = AdminSuspendAccountInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data["reason"]

        with transaction.atomic():
            user = (
                User.objects.select_for_update(of=("self",))
                .select_related("creator", "brand")
                .filter(pk=user_id)
                .first()
            )
            if user is None:
                raise NotFound("User not found.")

            if user.status == UserStatus.SUSPENDED:
                raise Conflict("User is already suspended.", code="ALREADY_SUSPENDED")

            user.status = UserStatus.SUSPENDED
            user.save(update_fields=["status", "updated_at"])

            # Invalidate all active sessions
            tokens = OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False)
            for t in tokens:
                BlacklistedToken.objects.get_or_create(token=t)

            # Side effects
            if user.role == Role.CREATOR and hasattr(user, "creator") and user.creator:
                user.creator.featured = False
                user.creator.featured_order = 0
                user.creator.save(update_fields=["featured", "featured_order", "updated_at"])

            elif user.role == Role.BRAND and hasattr(user, "brand") and user.brand:
                live_campaigns = Campaign.objects.filter(brand=user.brand, moderation_status=ModerationStatus.LIVE)
                for camp in live_campaigns:
                    camp.moderation_status = ModerationStatus.HIDDEN
                    camp.moderation_reason = f"Account suspended: {reason}"
                    camp.save(update_fields=["moderation_status", "moderation_reason", "updated_at"])
                    Bid.objects.filter(campaign=camp, status=BidStatus.PENDING).update(
                        status=BidStatus.HELD, updated_at=timezone.now()
                    )

            audit = record_audit(
                admin=request.user,
                action="account.suspend",
                target=user,
                detail={"reason": reason, "role": user.role},
            )

            notify(
                user_id=user.id,
                type="account_suspended",
                title="Account Suspended",
                body=f"Your account has been suspended: {reason}",
                data={"reason": reason},
            )

            return Response(
                {
                    "user": AdminAccountUserOutput(user).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Users"])
class AdminReactivateAccountView(APIView):
    """Reactivate a suspended user account."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=AdminReactivateAccountInput, responses={200: AdminAccountActionResult})
    def post(self, request: Request, user_id: UUID) -> Response:
        user_id = _resolve_user_id(user_id)
        input_serializer = AdminReactivateAccountInput(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        reason = input_serializer.validated_data.get("reason", "")

        with transaction.atomic():
            user = User.objects.select_for_update(of=("self",)).filter(pk=user_id).first()
            if user is None:
                raise NotFound("User not found.")

            if user.status == UserStatus.ACTIVE:
                raise Conflict("User is already active.", code="ALREADY_ACTIVE")

            user.status = UserStatus.ACTIVE
            user.save(update_fields=["status", "updated_at"])

            audit = record_audit(
                admin=request.user,
                action="account.reactivate",
                target=user,
                detail={"reason": reason, "role": user.role},
            )

            notify(
                user_id=user.id,
                type="account_reactivated",
                title="Account Reactivated",
                body="Your account has been reactivated.",
                data={},
            )

            return Response(
                {
                    "user": AdminAccountUserOutput(user).data,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Users"])
class AdminPasswordResetView(APIView):
    """Initiate a 30-minute password reset flow for a user without exposing credentials."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=None, responses={200: AdminPasswordResetResult})
    def post(self, request: Request, user_id: UUID) -> Response:
        user_id = _resolve_user_id(user_id)
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise NotFound("User not found.")

        destination = user.email or user.phone
        channel = OtpChannel.EMAIL if user.email else OtpChannel.SMS
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = timezone.now() + timedelta(minutes=30)

        with transaction.atomic():
            # Invalidate prior reset challenges
            OtpChallenge.objects.filter(
                destination__iexact=destination,
                purpose=OtpPurpose.PASSWORD_RESET,
                consumed_at__isnull=True,
            ).delete()

            challenge = OtpChallenge(
                channel=channel,
                destination=destination,
                purpose=OtpPurpose.PASSWORD_RESET,
                expires_at=expires_at,
            )
            challenge.code_hash = _hash_code(challenge.id, code)
            challenge.save()

            audit = record_audit(
                admin=request.user,
                action="account.password_reset",
                target=user,
                detail={"destination": destination, "expiresInMinutes": 30},
            )

            notify(
                user_id=user.id,
                type="password_reset_initiated",
                title="Password Reset Initiated",
                body="An administrator has initiated a password reset for your account.",
                data={"expiresInMinutes": 30},
            )

            return Response(
                {
                    "message": "Password reset initiated successfully.",
                    "expiresInMinutes": 30,
                    "audit": audit,
                }
            )


@extend_schema(tags=["Admin - Users"])
class AdminSessionsListView(APIView):
    """List active sessions (outstanding JWT tokens) for a specific user."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(responses={200: AdminSessionOutput(many=True)})
    def get(self, request: Request, user_id: UUID) -> Response:
        user_id = _resolve_user_id(user_id)
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            raise NotFound("User not found.")

        tokens = (
            OutstandingToken.objects.filter(user=user)
            .exclude(blacklistedtoken__isnull=False)
            .order_by("-created_at")
        )
        return Response(AdminSessionOutput(tokens, many=True).data)


@extend_schema(tags=["Admin - Users"])
class AdminSessionsRevokeView(APIView):
    """Revoke/blacklist all active sessions for a specific user."""

    permission_classes = [RequireAdminRoles(AdminRole.TRUST_SAFETY)]

    @extend_schema(request=None, responses={200: AdminSessionsRevokeResult})
    def post(self, request: Request, user_id: UUID) -> Response:
        user_id = _resolve_user_id(user_id)
        with transaction.atomic():
            user = User.objects.select_for_update(of=("self",)).filter(pk=user_id).first()
            if user is None:
                raise NotFound("User not found.")

            tokens = list(OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False))
            revoked_count = 0
            for t in tokens:
                BlacklistedToken.objects.get_or_create(token=t)
                revoked_count += 1

            audit = record_audit(
                admin=request.user,
                action="account.sessions_invalidate",
                target=user,
                detail={"revokedCount": revoked_count},
            )

            return Response(
                {
                    "revokedCount": revoked_count,
                    "audit": audit,
                }
            )
