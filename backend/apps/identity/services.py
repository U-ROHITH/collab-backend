import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import Conflict, DomainError, Forbidden, TooManyRequests
from apps.identity import providers
from apps.identity.models import AuditLog, OtpChallenge, OtpChannel, Role, User, UserStatus

logger = logging.getLogger(__name__)

_INDIAN_MOBILE = re.compile(r"^(?:\+?91)?([6-9]\d{9})$")


class OtpInvalid(DomainError):
    status_code = 400
    code = "OTP_INVALID"
    message = "The code is invalid or has expired."


class OtpDeliveryFailed(DomainError):
    status_code = 503
    code = "OTP_DELIVERY_FAILED"
    message = "We couldn't send the code right now. Please try again."


class AuthInvalid(DomainError):
    status_code = 401
    code = "AUTH_INVALID"
    message = "Invalid email/phone or password."


class GoogleTokenInvalid(DomainError):
    status_code = 400
    code = "GOOGLE_TOKEN_INVALID"
    message = "Google sign-in didn't go through. Please try again."


class GoogleEmailUnverified(DomainError):
    status_code = 400
    code = "GOOGLE_EMAIL_UNVERIFIED"
    message = "Your Google account's email isn't verified yet. Verify it with Google, or sign in with a code."


def normalize_destination(channel: str, raw: str) -> str:
    raw = raw.strip()
    if channel == OtpChannel.SMS:
        m = _INDIAN_MOBILE.match(re.sub(r"[\s\-()]", "", raw))
        if not m:
            raise DomainError("Enter a valid 10-digit Indian mobile number.", code="INVALID_PHONE")
        return f"+91{m.group(1)}"
    return raw.lower()


def _hash_code(challenge_id: UUID, code: str) -> str:
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{challenge_id}:{code}".encode(), hashlib.sha256).hexdigest()


def request_otp(*, channel: str, destination: str, ip: str | None) -> OtpChallenge:
    dest = normalize_destination(channel, destination)
    now = timezone.now()
    recent = OtpChallenge.objects.filter(destination=dest, created_at__gte=now - timedelta(hours=1)).count()
    if recent >= settings.OTP_MAX_PER_DESTINATION_PER_HOUR:
        raise TooManyRequests("Too many codes requested. Try again in an hour.")

    code = f"{secrets.randbelow(1_000_000):06d}"
    with transaction.atomic():
        # Only the newest code is valid.
        OtpChallenge.objects.filter(destination=dest, consumed_at__isnull=True).update(consumed_at=now)
        challenge = OtpChallenge(
            channel=channel,
            destination=dest,
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
            requester_ip=ip,
        )
        challenge.code_hash = _hash_code(challenge.id, code)
        challenge.save()

    try:
        if channel == OtpChannel.SMS:
            providers.send_sms_otp(dest, code)
        else:
            providers.send_email_otp(dest, code)
    except providers.DeliveryError:
        raise OtpDeliveryFailed() from None
    return challenge


@dataclass(frozen=True)
class AuthResult:
    user: User
    access: str
    refresh: str


def verify_otp(*, challenge_id: UUID, code: str) -> AuthResult:
    # Attempt counting must commit even when the code is wrong, so the check happens
    # in its own transaction and the error is raised after commit.
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().filter(pk=challenge_id).first()
        if challenge is None or challenge.consumed_at or challenge.expires_at <= timezone.now():
            ok = False
        elif challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            ok = False
        else:
            challenge.attempts += 1
            ok = hmac.compare_digest(challenge.code_hash, _hash_code(challenge.id, code))
            if ok:
                challenge.consumed_at = timezone.now()
            challenge.save(update_fields=["attempts", "consumed_at", "updated_at"])
    if not ok:
        raise OtpInvalid()

    user = _get_or_create_user(challenge.channel, challenge.destination)
    if not user.is_active:
        raise OtpInvalid()
    return issue_tokens(user)


def _get_or_create_user(channel: str, destination: str) -> User:
    lookup = {"phone": destination} if channel == OtpChannel.SMS else {"email": destination}
    user = User.objects.filter(**lookup).first()
    if user:
        if channel == OtpChannel.EMAIL and not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified", "updated_at"])
        return user
    try:
        user = User.objects.create_user(**lookup)
        if channel == OtpChannel.EMAIL:
            user.email_verified = True
            user.save(update_fields=["email_verified", "updated_at"])
        return user
    except IntegrityError:  # concurrent first login
        user = User.objects.get(**lookup)
        if channel == OtpChannel.EMAIL and not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified", "updated_at"])
        return user


def request_email_verification(*, user: User, email: str, ip: str | None) -> OtpChallenge:
    dest = normalize_destination(OtpChannel.EMAIL, email)
    if User.objects.filter(email=dest).exclude(pk=user.pk).exists():
        raise Conflict("This email is already in use by another account.", code="EMAIL_ALREADY_IN_USE")
    return request_otp(channel=OtpChannel.EMAIL, destination=dest, ip=ip)


def verify_email_challenge(*, user: User, challenge_id: UUID, code: str) -> User:
    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().filter(pk=challenge_id).first()
        if challenge is None or challenge.consumed_at or challenge.expires_at <= timezone.now():
            ok = False
        elif challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            ok = False
        elif challenge.channel != OtpChannel.EMAIL:
            ok = False
        else:
            challenge.attempts += 1
            ok = hmac.compare_digest(challenge.code_hash, _hash_code(challenge.id, code))
            if ok:
                challenge.consumed_at = timezone.now()
            challenge.save(update_fields=["attempts", "consumed_at", "updated_at"])
    if not ok:
        raise OtpInvalid()

    verified_email = challenge.destination
    with transaction.atomic():
        if User.objects.filter(email=verified_email).exclude(pk=user.pk).exists():
            raise Conflict("This email is already in use by another account.", code="EMAIL_ALREADY_IN_USE")
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        locked_user.email = verified_email
        locked_user.email_verified = True
        locked_user.save(update_fields=["email", "email_verified", "updated_at"])
        AuditLog.objects.create(
            actor=locked_user,
            action="user.email_verified",
            entity_type="user",
            entity_id=str(locked_user.pk),
            metadata={"email": verified_email},
        )
    return locked_user


def issue_tokens(user: User) -> AuthResult:
    refresh = RefreshToken.for_user(user)
    return AuthResult(user=user, access=str(refresh.access_token), refresh=str(refresh))


def find_user_by_identifier(ident: str) -> User | None:
    ident = ident.strip()
    if not ident:
        return None
    if "@" in ident:
        return User.objects.filter(email__iexact=ident).first()
    # Phone normalization
    clean_digits = re.sub(r"[\s\-()]", "", ident)
    m = _INDIAN_MOBILE.match(clean_digits)
    candidates = [ident]
    if m:
        candidates.extend([f"+91{m.group(1)}", m.group(1)])
    return User.objects.filter(Q(phone__in=candidates) | Q(email__iexact=ident)).first()


def login_with_password(*, identifier: str, password: str) -> AuthResult:
    user = find_user_by_identifier(identifier)
    if user is None:
        raise AuthInvalid()

    if not user.has_usable_password():
        raise AuthInvalid()

    if not user.check_password(password):
        raise AuthInvalid()

    if user.status == UserStatus.SUSPENDED:
        raise Forbidden("Your account has been suspended.", code="ACCOUNT_SUSPENDED")

    if user.status == UserStatus.DEACTIVATED or not user.is_active:
        raise Forbidden("Your account has been deactivated.", code="ACCOUNT_DEACTIVATED")

    AuditLog.objects.create(
        actor=user,
        action="user.login_password",
        entity_type="user",
        entity_id=str(user.pk),
        metadata={"identifier": identifier},
    )
    return issue_tokens(user)


def verify_google_id_token(token: str) -> dict[str, Any]:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    audience = settings.GOOGLE_OAUTH_CLIENT_ID or None
    try:
        req = google_requests.Request()
        idinfo = id_token.verify_oauth2_token(token, req, audience=audience)
    except Exception as exc:
        logger.warning("Google ID token verification failed: %s", exc)
        raise GoogleTokenInvalid() from None

    email = idinfo.get("email")
    if not email:
        raise GoogleTokenInvalid("Google token does not contain an email address.")

    if not idinfo.get("email_verified", False):
        raise GoogleEmailUnverified()

    return idinfo


def authenticate_with_google(*, token: str, role: str | None = None) -> AuthResult:
    payload = verify_google_id_token(token)
    email = payload["email"].lower().strip()
    full_name = payload.get("name", "").strip()

    user = User.objects.filter(email__iexact=email).first()
    if user:
        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified", "updated_at"])
    else:
        try:
            with transaction.atomic():
                user = User.objects.create_user(
                    email=email,
                    full_name=full_name,
                    email_verified=True,
                )
        except IntegrityError:
            user = User.objects.get(email__iexact=email)

    if user.status == UserStatus.SUSPENDED:
        raise Forbidden("Your account has been suspended.", code="ACCOUNT_SUSPENDED")

    if user.status == UserStatus.DEACTIVATED or not user.is_active:
        raise Forbidden("Your account has been deactivated.", code="ACCOUNT_DEACTIVATED")

    AuditLog.objects.create(
        actor=user,
        action="user.login_google",
        entity_type="user",
        entity_id=str(user.pk),
        metadata={"email": email},
    )
    return issue_tokens(user)


def complete_signup(
    *,
    user: User,
    role: str,
    full_name: str,
    brand_name: str = "",
    display_name: str = "",
    password: str | None = None,
) -> User:
    """Set role exactly once and create the matching profile. `admin` is never allowed here."""
    from apps.brands.services import create_brand_for_user
    from apps.creators.services import create_creator_for_user

    if role not in (Role.BRAND, Role.CREATOR):
        raise DomainError("Invalid role.", code="INVALID_ROLE")
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if locked.role is not None:
            raise Conflict("Signup is already complete.", code="SIGNUP_ALREADY_COMPLETE")
        locked.role = role
        locked.full_name = full_name
        update_fields = ["role", "full_name", "updated_at"]
        if password:
            locked.set_password(password)
            update_fields.append("password")
        locked.save(update_fields=update_fields)
        if role == Role.BRAND:
            create_brand_for_user(user=locked, name=brand_name or full_name)
        else:
            create_creator_for_user(user=locked, display_name=display_name or full_name)
        AuditLog.objects.create(
            actor=locked,
            action="signup.completed",
            entity_type="user",
            entity_id=str(locked.pk),
            metadata={"role": role, "password_set": bool(password)},
        )
    return locked


def grant_admin(*, email: str, password: str) -> User:
    """Used only by the `grant_admin` management command."""
    with transaction.atomic():
        user = User.objects.filter(email=email.lower()).first()
        if user is None:
            user = User.objects.create_superuser(email=email.lower(), password=password)
        else:
            user.role, user.is_staff, user.is_superuser = Role.ADMIN, True, True
            user.set_password(password)
            user.save()
        AuditLog.objects.create(actor=None, action="admin.granted", entity_type="user", entity_id=str(user.pk))
    return user
