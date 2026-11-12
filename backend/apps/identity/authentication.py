from typing import Any

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class ActiveUserJWTAuthentication(JWTAuthentication):
    """Verifies our JWT, then loads the user from the DB on every request.

    Role and status always come from the DB (never from token claims), so suspending a user or
    changing a role takes effect immediately. simplejwt rejects users whose `is_active` is False.
    Tokens issued before the password last changed are rejected at once (`pwv` claim), so a reset
    locks out a stolen session immediately rather than when its access token expires.
    """

    def get_user(self, validated_token: Any) -> Any:
        from apps.identity.services import password_version

        user = super().get_user(validated_token)
        issued_for = validated_token.get("pwv")
        # Only accounts with a password have one to protect; this also keeps sessions issued before
        # this check existed (older fingerprint format) working for code-only accounts.
        if issued_for and user.has_usable_password() and issued_for != password_version(user):
            raise AuthenticationFailed("Your password was changed. Please sign in again.", code="password_changed")
        return user


try:  # register the bearer scheme with drf-spectacular so the OpenAPI schema documents auth
    from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme

    class ActiveUserJWTScheme(SimpleJWTScheme):
        target_class = "apps.identity.authentication.ActiveUserJWTAuthentication"
        name = "bearerAuth"
except ImportError:  # pragma: no cover
    pass
