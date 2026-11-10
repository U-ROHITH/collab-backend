from typing import Any

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
    emailVerified = serializers.BooleanField(source="email_verified")  # noqa: N815
    needsOnboarding = serializers.SerializerMethodField()  # noqa: N815
    profileId = serializers.SerializerMethodField()  # noqa: N815

    class Meta:
        model = User
        fields = ("id", "email", "emailVerified", "phone", "fullName", "role", "status", "needsOnboarding", "profileId")

    def get_needsOnboarding(self, obj: User) -> bool:  # noqa: N802
        return obj.role is None

    def get_profileId(self, obj: User) -> str | None:  # noqa: N802
        profile = getattr(obj, "brand", None) if obj.role == Role.BRAND else getattr(obj, "creator", None)
        return str(profile.id) if profile else None


class MeUpdateInput(StrictSerializer):
    email = serializers.EmailField(required=False)
    fullName = serializers.CharField(max_length=120, required=False)  # noqa: N815


class EmailVerificationRequestInput(StrictSerializer):
    email = serializers.EmailField()


class EmailVerificationRequestOutput(serializers.Serializer):
    challengeId = serializers.UUIDField()  # noqa: N815
    expiresAt = serializers.DateTimeField()  # noqa: N815
    expiresIn = serializers.IntegerField(required=False)  # noqa: N815


class EmailVerificationVerifyInput(StrictSerializer):
    challengeId = serializers.UUIDField()  # noqa: N815
    code = serializers.RegexField(r"^\d{6}$")


class EmailVerificationVerifyOutput(serializers.Serializer):
    email = serializers.EmailField()
    emailVerified = serializers.BooleanField()  # noqa: N815


class AuthTokensOutput(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = MeOutput()


class SignupCompleteInput(StrictSerializer):
    role = serializers.ChoiceField(choices=[Role.BRAND, Role.CREATOR])
    fullName = serializers.CharField(max_length=120)  # noqa: N815
    brandName = serializers.CharField(max_length=120, required=False, allow_blank=True)  # noqa: N815
    displayName = serializers.CharField(max_length=80, required=False, allow_blank=True)  # noqa: N815
    password = serializers.CharField(max_length=128, required=False, write_only=True)
    confirmPassword = serializers.CharField(max_length=128, required=False, write_only=True)  # noqa: N815

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "confirm_password" in data:
                if "confirmPassword" not in data:
                    data["confirmPassword"] = data["confirm_password"]
                del data["confirm_password"]
        return super().to_internal_value(data)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        password = attrs.get("password")
        confirm_password = attrs.get("confirmPassword")

        if password or confirm_password:
            if not password:
                raise serializers.ValidationError({"password": "Password is required."})
            if not confirm_password:
                raise serializers.ValidationError({"confirmPassword": "Confirm password is required."})
            if len(password) < 8:
                raise serializers.ValidationError({"password": "Password must be at least 8 characters long."})
            if password != confirm_password:
                raise serializers.ValidationError({"confirmPassword": "Passwords do not match."})

        return attrs


class RefreshInput(StrictSerializer):
    refresh = serializers.CharField()
