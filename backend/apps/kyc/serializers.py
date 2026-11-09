import re

from rest_framework import serializers

from apps.core.serializers import StrictSerializer
from apps.kyc.models import IdType

_PAN = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


class PanInput(StrictSerializer):
    pan = serializers.CharField(max_length=10, help_text="10 characters, e.g. ABCDE1234F")
    nameOnPan = serializers.CharField(max_length=120)  # noqa: N815

    def validate_pan(self, value: str) -> str:
        value = value.strip().upper()
        if not _PAN.match(value):
            raise serializers.ValidationError("Enter a valid PAN (e.g. ABCDE1234F).")
        return value


class IdDocumentInput(StrictSerializer):
    idType = serializers.ChoiceField(choices=IdType.choices)  # noqa: N815
    uploadId = serializers.UUIDField(help_text="A READY upload of kind kyc_document")  # noqa: N815


class BankInput(StrictSerializer):
    accountHolderName = serializers.CharField(max_length=120)  # noqa: N815
    accountNumber = serializers.RegexField(r"^\d{9,18}$", help_text="9-18 digits")  # noqa: N815
    ifsc = serializers.CharField(max_length=11)

    def validate_ifsc(self, value: str) -> str:
        value = value.strip().upper()
        if not _IFSC.match(value):
            raise serializers.ValidationError("Enter a valid IFSC (e.g. HDFC0001234).")
        return value


class _PanStep(serializers.Serializer):
    done = serializers.BooleanField()
    nameOnPan = serializers.CharField(allow_null=True)  # noqa: N815
    panLast4 = serializers.CharField(allow_null=True)  # noqa: N815


class _IdStep(serializers.Serializer):
    done = serializers.BooleanField()
    idType = serializers.CharField(allow_null=True)  # noqa: N815


class _BankStep(serializers.Serializer):
    done = serializers.BooleanField()
    accountHolderName = serializers.CharField(allow_null=True)  # noqa: N815
    accountLast4 = serializers.CharField(allow_null=True)  # noqa: N815
    ifsc = serializers.CharField(allow_null=True)


class _Steps(serializers.Serializer):
    pan = _PanStep()
    id = _IdStep()
    bank = _BankStep()


class KycOutput(serializers.Serializer):
    status = serializers.ChoiceField(choices=["not_started", "in_progress", "pending", "verified", "rejected"])
    rejectionReason = serializers.CharField(allow_null=True)  # noqa: N815
    submittedAt = serializers.DateTimeField(allow_null=True)  # noqa: N815
    steps = _Steps()
