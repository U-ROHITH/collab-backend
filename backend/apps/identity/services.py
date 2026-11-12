import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.exceptions import Conflict, DomainError, TooManyRequests
from apps.identity import google, providers
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
    # One message for every failure (wrong password, unknown account, no password yet) so it can't be used
    # to learn which numbers have accounts; the second sentence covers code-only accounts.
    message = "Incorrect email/phone or password. If you signed up with a code, sign in with a code and set a password."


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


def _test_code_for(dest: str) -> str | None:
    """Fixed code for configured test logins (QA / app-store review), else None."""
    code = settings.OTP_TEST_CODE
    if code and re.fullmatch(r"\d{6}", code) and dest in {d.strip().lower() for d in settings.OTP_TEST_ACCOUNTS}:
        return code
    return None


def request_otp(*, channel: str, destination: str, ip: str | None) -> OtpChallenge:
    dest = normalize_destination(channel, destination)
    now = timezone.now()
    test_code = _test_code_for(dest)
    recent = OtpChallenge.objects.filter(destination=dest, created_at__gte=now - timedelta(hours=1)).count()
    if test_code is None and recent >= settings.OTP_MAX_PER_DESTINATION_PER_HOUR:
        raise TooManyRequests("Too many codes requested. Try again in an hour.")

    code = test_code or f"{secrets.randbelow(1_000_000):06d}"
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

    if test_code is not None:
        logger.warning("Test login code issued for a configured test account (challenge %s)", challenge.id)
        return challenge
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
    challenge = _consume_challenge(challenge_id=challenge_id, code=code)
    user = _get_or_create_user(challenge.channel, challenge.destination)
    if not user.is_active:
        raise OtpInvalid()
    return issue_tokens(user, method="otp")


def _consume_challenge(*, challenge_id: UUID, code: str) -> OtpChallenge:
    """Check a code and mark it used. Raises OtpInvalid."""
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
    return challenge


class GoogleSignInFailed(DomainError):
    status_code = 401
    code = "GOOGLE_TOKEN_INVALID"
    message = "Google sign-in failed. Please try again."


def google_sign_in(*, id_token: str, role: str | None) -> AuthResult:
    """Sign in (or sign up) with a Google ID token.

    The account is matched on the Google-verified email, so a person who used email OTP before lands
    in the same account. `role` only applies to an account that hasn't picked one yet; an existing
    account keeps its role (the frontend reads `user.role` from the response).
    """
    try:
        claims = google.verify_id_token(id_token)
    except google.GoogleTokenError:
        raise GoogleSignInFailed() from None
    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is not True:
        raise GoogleSignInFailed("Your Google account's email isn't verified.", code="GOOGLE_EMAIL_UNVERIFIED")

    user = _get_or_create_user(OtpChannel.EMAIL, email)
    if not user.is_active:
        raise GoogleSignInFailed()
    if user.role is None and role:
        name = str(claims.get("name") or "").strip()[:120] or email.split("@")[0]
        try:
            user = complete_signup(user=user, role=role, full_name=name)
        except Conflict:  # a concurrent request finished signup first
            user = User.objects.get(pk=user.pk)
    return issue_tokens(user, method="google")


def _get_or_create_user(channel: str, destination: str) -> User:
    lookup = {"phone": destination} if channel == OtpChannel.SMS else {"email": destination}
    user = User.objects.filter(**lookup).first()
    if user:
        return user
    try:
        return User.objects.create_user(**lookup)
    except IntegrityError:  # concurrent first login
        return User.objects.get(**lookup)


# How a session was opened. Carried in every token (refresh keeps the original values), so endpoints can ask
# "did this person prove control of their email/phone just now?" without another round trip.
AUTH_METHODS = ("otp", "google", "password")


def _password_version(user: User) -> str:
    """Short fingerprint of the stored hash: changes whenever the password does. Not a secret by itself."""
    return hashlib.sha256(user.password.encode()).hexdigest()[:12]


def issue_tokens(user: User, *, method: str | None = None) -> AuthResult:
    refresh = RefreshToken.for_user(user)
    if method:
        refresh["amr"] = method
        refresh["auth_time"] = int(timezone.now().timestamp())
        refresh["pwv"] = _password_version(user)
    return AuthResult(user=user, access=str(refresh.access_token), refresh=str(refresh))


# --- Password sign-in ------------------------------------------------------------------------------
# Per-account cap on wrong passwords, on top of the per-IP throttle, so a spread-out attack on one
# account still stops. Counts by the identifier as typed (normalized), so unknown accounts are capped too.
LOGIN_MAX_FAILURES = 10
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60


def _login_fail_key(normalized_identifier: str) -> str:
    return f"pwlogin:fail:{hashlib.sha256(normalized_identifier.encode()).hexdigest()}"


def _bump(key: str) -> int:
    if cache.add(key, 1, LOGIN_FAILURE_WINDOW_SECONDS):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, LOGIN_FAILURE_WINDOW_SECONDS)
        return 1


def _find_user_for_login(identifier: str) -> tuple[str, User | None]:
    """Return (normalized identifier, user). Emails match case-insensitively; phones in any common format."""
    ident = identifier.strip()
    if "@" in ident:
        key = ident.lower()
        return key, User.objects.filter(email__iexact=key).first()
    digits = re.sub(r"[\s\-()]", "", ident)
    if len(digits) == 11 and digits.startswith("0"):  # trunk prefix: 098765 43210
        digits = digits[1:]
    m = _INDIAN_MOBILE.match(digits)
    if not m:
        return digits, None
    phone = f"+91{m.group(1)}"
    return phone, User.objects.filter(phone=phone).first()


def login_with_password(*, identifier: str, password: str) -> AuthResult:
    key, user = _find_user_for_login(identifier)
    fail_key = _login_fail_key(key)
    # Count the attempt before checking, so parallel guesses can't all slip in under the limit.
    if _bump(fail_key) > LOGIN_MAX_FAILURES:
        raise TooManyRequests("Too many sign-in attempts. Try again in 15 minutes, or sign in with a code.")

    if user is None or not user.has_usable_password():
        make_password(password)  # same hashing cost as a real check, so timing doesn't reveal the account
        raise AuthInvalid()
    if not (user.check_password(password) and user.status == UserStatus.ACTIVE):
        raise AuthInvalid()

    cache.delete(fail_key)
    AuditLog.objects.create(
        actor=user, action="user.login_password", entity_type="user", entity_id=str(user.pk), metadata={}
    )
    return issue_tokens(user, method="password")


PASSWORD_CHANGE_FRESH_SECONDS = 10 * 60


class ReauthRequired(DomainError):
    status_code = 403
    code = "REAUTH_REQUIRED"
    message = "Enter your current password, or sign in with a code again to set a new one."


def reset_password(*, challenge_id: UUID, code: str, password: str) -> AuthResult:
    """Forgot password: prove control of the email/phone with a fresh code, then set a new password."""
    challenge = _consume_challenge(challenge_id=challenge_id, code=code)
    user = _get_or_create_user(challenge.channel, challenge.destination)
    if not user.is_active:
        raise OtpInvalid()
    _set_password_everywhere(user, password, action="user.password_reset")
    cache.delete(_login_fail_key(challenge.destination))
    return issue_tokens(user, method="otp")


def change_password(
    *,
    user: User,
    password: str,
    current_password: str | None,
    auth_method: str | None,
    auth_time: int | None,
    password_version: str | None,
) -> AuthResult:
    """Set a new password while signed in.

    Allowed with the current password, or without one when the session was opened by a code or Google in the
    last few minutes (the "forgot password -> sign in with a code -> set a new one" flow, and first-time setup
    for code-only accounts). A token alone isn't enough, so a stolen token can't become a permanent takeover.
    """
    if current_password:
        attempts_key = f"pwchange:fail:{user.pk}"
        if _bump(attempts_key) > LOGIN_MAX_FAILURES:
            raise TooManyRequests("Too many attempts. Try again in 15 minutes, or sign in with a code.")
        if not user.check_password(current_password):
            raise ValidationError({"currentPassword": ["That's not your current password."]})
        cache.delete(attempts_key)
    else:
        fresh = auth_time is not None and timezone.now().timestamp() - auth_time <= PASSWORD_CHANGE_FRESH_SECONDS
        # A token from before the last password change can't skip the current password, even if still fresh.
        same_password = password_version == _password_version(user)
        if not (auth_method in ("otp", "google") and fresh and same_password):
            raise ReauthRequired()
    _set_password_everywhere(user, password, action="user.password_changed")
    return issue_tokens(user, method="password")


def _set_password_everywhere(user: User, password: str, *, action: str) -> None:
    """Set the password and revoke every refresh token. Access tokens already issued lapse within 15 minutes."""
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        locked.set_password(password)
        locked.save(update_fields=["password", "updated_at"])
        BlacklistedToken.objects.bulk_create(
            [BlacklistedToken(token=t) for t in OutstandingToken.objects.filter(user=locked)],
            ignore_conflicts=True,
        )
        AuditLog.objects.create(actor=locked, action=action, entity_type="user", entity_id=str(locked.pk), metadata={})
    user.password = locked.password


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
        fields = ["role", "full_name", "updated_at"]
        if password:
            locked.set_password(password)
            fields.append("password")
        locked.save(update_fields=fields)
        if role == Role.BRAND:
            create_brand_for_user(user=locked, name=brand_name or full_name)
        else:
            create_creator_for_user(user=locked, display_name=display_name or full_name)
        AuditLog.objects.create(
            actor=locked,
            action="signup.completed",
            entity_type="user",
            entity_id=str(locked.pk),
            metadata={"role": role, "passwordSet": bool(password)},
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
