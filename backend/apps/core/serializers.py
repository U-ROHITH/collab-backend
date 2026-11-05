"""Strict serializers: DRF silently ignores unknown fields; we reject them (anti mass-assignment)."""

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers


class StrictMixin:
    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, Mapping):
            fields = self.fields  # type: ignore[attr-defined]
            writable = {name for name, f in fields.items() if not f.read_only}
            bad = sorted(set(data) - writable)
            if bad:
                raise serializers.ValidationError({k: ["Unknown or read-only field."] for k in bad})
        return super().to_internal_value(data)  # type: ignore[misc]


class StrictSerializer(StrictMixin, serializers.Serializer):
    pass


class HttpsURLField(serializers.URLField):
    """Only https URLs. The server never fetches these (no SSRF surface)."""

    def to_internal_value(self, data: Any) -> str:
        value = super().to_internal_value(data)
        if not value.lower().startswith("https://"):
            raise serializers.ValidationError("Only https:// URLs are allowed.")
        return value


class MoneyField(serializers.IntegerField):
    """Integer paise. Bounds: ₹1 .. ₹1 crore."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("min_value", 100)
        kwargs.setdefault("max_value", 1_000_000_000)
        super().__init__(**kwargs)


class UsageRightsSerializer(StrictSerializer):
    DURATIONS = ("perpetual", "12m", "6m", "3m")
    CHANNELS = ("paid_ads", "organic_social", "website")

    duration = serializers.ChoiceField(choices=DURATIONS)
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=CHANNELS), allow_empty=False, max_length=len(CHANNELS)
    )
    exclusivity = serializers.BooleanField()
    exclusivityCategory = serializers.CharField(max_length=80, required=False, allow_blank=True)  # noqa: N815
    territory = serializers.CharField(max_length=80)
    city = serializers.CharField(max_length=80, required=False, allow_blank=True)

    def validate_channels(self, value: list[str]) -> list[str]:
        return sorted(set(value))
