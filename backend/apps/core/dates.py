"""Date helpers. Business dates (closing dates, deadlines) are calendar days in India time (IST).

A date-only deadline like 2026-11-20 means "until the end of 20 Nov in India", stored as
2026-11-20T23:59:59.999999+05:30 so comparisons with `now()` stay simple and correct.
"""

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

IST = ZoneInfo("Asia/Kolkata")


def end_of_day_ist(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=IST)


def start_of_day_ist(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=IST)


def ist_date(dt: datetime) -> date:
    return timezone.localtime(dt, IST).date()


def ist_time(dt: datetime) -> str:
    return timezone.localtime(dt, IST).strftime("%H:%M")


@extend_schema_field(OpenApiTypes.DATE)
class EndOfDayDateField(serializers.Field):
    """Input: "YYYY-MM-DD" (a full ISO datetime is also accepted; only its IST date is used).
    Stored as the end of that day in IST. Output: "YYYY-MM-DD" (no time)."""

    default_error_messages = {"invalid": "Enter a date as YYYY-MM-DD."}

    def to_internal_value(self, data: Any) -> datetime:
        if isinstance(data, str) and len(data) == 10:
            try:
                return end_of_day_ist(date.fromisoformat(data))
            except ValueError:
                self.fail("invalid")
        try:
            dt = serializers.DateTimeField().to_internal_value(data)
        except serializers.ValidationError:
            self.fail("invalid")
        return end_of_day_ist(ist_date(dt))

    def to_representation(self, value: datetime) -> str:
        return ist_date(value).isoformat()


@extend_schema_field(OpenApiTypes.DATE)
class LocalDateField(serializers.ReadOnlyField):
    """Output-only: a timestamp shown as its IST calendar date, "YYYY-MM-DD"."""

    def to_representation(self, value: datetime) -> str | None:
        return ist_date(value).isoformat() if value else None
