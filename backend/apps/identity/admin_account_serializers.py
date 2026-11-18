from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.kyc.admin_serializers import AdminAuditOutput


class AdminSuspendAccountInput(StrictSerializer):
    reason = serializers.CharField(min_length=4, max_length=500)


class AdminReactivateAccountInput(StrictSerializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class AdminAccountUserOutput(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField(allow_null=True)
    phone = serializers.CharField(allow_null=True)
    role = serializers.CharField()
    status = serializers.CharField()
    adminRole = serializers.CharField(source="admin_role", allow_null=True)  # noqa: N815


class AdminAccountActionResult(serializers.Serializer):
    user = AdminAccountUserOutput()
    audit = AdminAuditOutput()


class AdminSessionOutput(serializers.Serializer):
    id = serializers.IntegerField()
    jti = serializers.CharField()
    createdAt = serializers.DateTimeField(source="created_at")  # noqa: N815
    expiresAt = serializers.DateTimeField(source="expires_at")  # noqa: N815


class AdminSessionsRevokeResult(serializers.Serializer):
    revokedCount = serializers.IntegerField()  # noqa: N815
    audit = AdminAuditOutput()


class AdminPasswordResetResult(serializers.Serializer):
    message = serializers.CharField()
    expiresInMinutes = serializers.IntegerField()  # noqa: N815
    audit = AdminAuditOutput()

