from rest_framework_simplejwt.authentication import JWTAuthentication


class ActiveUserJWTAuthentication(JWTAuthentication):
    """Verifies our JWT, then loads the user from the DB on every request.

    Role and status always come from the DB (never from token claims), so suspending a user or
    changing a role takes effect immediately. simplejwt rejects users whose `is_active` is False.
    """


try:  # register the bearer scheme with drf-spectacular so the OpenAPI schema documents auth
    from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme

    class ActiveUserJWTScheme(SimpleJWTScheme):
        target_class = "apps.identity.authentication.ActiveUserJWTAuthentication"
        name = "bearerAuth"
except ImportError:  # pragma: no cover
    pass
