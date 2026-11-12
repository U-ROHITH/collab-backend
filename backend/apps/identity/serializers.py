from typing import Any

from django.contrib.auth.password_validation import get_password_validators, validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.identity.models import OtpChannel, Role, User


class OtpRequestInput(StrictSerializer):
    channel = serializers.ChoiceField(choices=OtpChannel.choices)
    destination = serializers.CharField(max_length=254)

    def validate(self, attrs: dict) -> dict:
        if attrs["channel"] == OtpChannel.EMAIL:
            serializers.EmailField().run_validation(attrs["destination"])
        return attrs


class OtpRequestOutput(serializers.Serializer):
    challengeId = serializers.UUIDField()  # noqa: N815
    expiresIn = serializers.IntegerField()  # noqa: N815


class OtpVerifyInput(StrictSerializer):
    challengeId = serializers.UUIDField()  # noqa: N815
    code = serializers.RegexField(r"^\d{6}$")


class MeOutput(serializers.ModelSerializer):
    fullName = serializers.CharField(source="full_name")  # noqa: N815
    needsOnboarding = serializers.SerializerMethodField()  # noqa: N815
    profileId = serializers.SerializerMethodField()  # noqa: N815
    kycStatus = serializers.SerializerMethodField()  # noqa: N815
    hasPassword = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "phone",
            "fullName",
            "role",
            "status",
            "needsOnboarding",
            "profileId",
            "kycStatus",
            "hasPassword",
        )

    def get_hasPassword(self, obj: User) -> bool:  # noqa: N802
        """False for accounts created with a code or Google that never set a password."""
        return obj.has_usable_password()

    def get_kycStatus(self, obj: User) -> str:  # noqa: N802
        """not_started | in_progress | pending | verified | rejected"""
        from apps.kyc.services import status_of

        return status_of(obj)

    def get_needsOnboarding(self, obj: User) -> bool:  # noqa: N802
        return obj.role is None

    def get_profileId(self, obj: User) -> str | None:  # noqa: N802
        profile = getattr(obj, "brand", None) if obj.role == Role.BRAND else getattr(obj, "creator", None)
        return str(profile.id) if profile else None


class AuthTokensOutput(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = MeOutput()


# App users' password rules (8+ characters, per the API reference). The stricter AUTH_PASSWORD_VALIDATORS
# setting (12+) stays for Django admin accounts.
_APP_PASSWORD_VALIDATORS = get_password_validators(
    [
        {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
        {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
        {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    ]
)


def check_new_password(attrs: dict[str, Any], user: User | None, *, required: bool) -> None:
    """Validate password + confirmPassword. Errors are keyed by field so the form can show them in place."""
    password = attrs.get("password")
    confirm = attrs.get("confirmPassword")
    if not password and not confirm and not required:
        return
    if not password:
        raise serializers.ValidationError({"password": ["Enter a password."]})
    if not confirm:
        raise serializers.ValidationError({"confirmPassword": ["Confirm your password."]})
    try:
        validate_password(password, user=user, password_validators=_APP_PASSWORD_VALIDATORS)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({"password": list(exc.messages)}) from None
    if password != confirm:
        raise serializers.ValidationError({"confirmPassword": ["Passwords don't match."]})


class SignupCompleteInput(StrictSerializer):
    role = serializers.ChoiceField(choices=[Role.BRAND, Role.CREATOR])
    fullName = serializers.CharField(max_length=120)  # noqa: N815
    brandName = serializers.CharField(max_length=120, required=False, allow_blank=True)  # noqa: N815
    displayName = serializers.CharField(max_length=80, required=False, allow_blank=True)  # noqa: N815
    password = serializers.CharField(max_length=128, required=False, write_only=True, trim_whitespace=False)
    confirmPassword = serializers.CharField(  # noqa: N815
        max_length=128, required=False, write_only=True, trim_whitespace=False
    )

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        check_new_password(attrs, self.context.get("user"), required=False)
        return attrs


class LoginInput(StrictSerializer):
    identifier = serializers.CharField(max_length=254, help_text="Email address or mobile number.")
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)


class PasswordResetInput(StrictSerializer):
    challengeId = serializers.UUIDField()  # noqa: N815
    code = serializers.RegexField(r"^\d{6}$")
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)
    confirmPassword = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)  # noqa: N815

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        check_new_password(attrs, None, required=True)
        return attrs


class RefreshInput(StrictSerializer):
    refresh = serializers.CharField()


class GoogleSignInInput(StrictSerializer):
    idToken = serializers.CharField(max_length=4096)  # noqa: N815
    role = serializers.ChoiceField(
        choices=[Role.BRAND, Role.CREATOR],
        required=False,
        allow_null=True,
        help_text="Used only when the account has no role yet (first sign-in).",
    )


class PasswordChangeInput(StrictSerializer):
    currentPassword = serializers.CharField(  # noqa: N815
        max_length=128, required=False, write_only=True, trim_whitespace=False
    )
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)
    confirmPassword = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)  # noqa: N815

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        check_new_password(attrs, self.context.get("user"), required=True)
        return attrs
