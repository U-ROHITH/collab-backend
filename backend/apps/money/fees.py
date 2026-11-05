"""Pure money math. No Django imports. All amounts are integer paise; percentages are basis points.

Invariant (enforced here AND by a DB CheckConstraint on orders):
    brand_total == creator_payout + platform_fee + gst + tds
"""

from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

BPS_DENOMINATOR = 10_000


class MoneyError(ValueError):
    pass


@dataclass(frozen=True)
class FeeConfig:
    version: str
    commission_bps: int
    gst_bps: int
    tds_bps: int
    tds_section: str
    commission_bearer: str  # "brand" | "creator"

    def __post_init__(self) -> None:
        for name in ("commission_bps", "gst_bps", "tds_bps"):
            value = getattr(self, name)
            if not isinstance(value, int) or not 0 <= value <= BPS_DENOMINATOR:
                raise MoneyError(f"{name} must be an int between 0 and {BPS_DENOMINATOR}")
        if self.commission_bearer not in ("brand", "creator"):
            raise MoneyError("commission_bearer must be 'brand' or 'creator'")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeeConfig":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OrderAmounts:
    subtotal: int
    platform_fee: int
    gst: int
    tds: int
    creator_payout: int
    brand_total: int


def percent_of(amount_paise: int, bps: int) -> int:
    """bps of amount, rounded half-up to the nearest paisa."""
    value = Decimal(amount_paise) * Decimal(bps) / Decimal(BPS_DENOMINATOR)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def compute_order_amounts(subtotal_paise: int, cfg: FeeConfig) -> OrderAmounts:
    if not isinstance(subtotal_paise, int) or isinstance(subtotal_paise, bool) or subtotal_paise <= 0:
        raise MoneyError("subtotal must be a positive integer number of paise")

    platform_fee = percent_of(subtotal_paise, cfg.commission_bps)
    gst = percent_of(platform_fee, cfg.gst_bps)  # GST applies to OUR fee, not the creator's price

    if cfg.commission_bearer == "brand":
        brand_total = subtotal_paise + platform_fee + gst
        creator_gross = subtotal_paise
    else:
        brand_total = subtotal_paise
        creator_gross = subtotal_paise - platform_fee - gst

    tds = percent_of(creator_gross, cfg.tds_bps)
    creator_payout = creator_gross - tds
    if creator_payout <= 0:
        raise MoneyError("fees exceed the order value")

    amounts = OrderAmounts(subtotal_paise, platform_fee, gst, tds, creator_payout, brand_total)
    if amounts.brand_total != amounts.creator_payout + amounts.platform_fee + amounts.gst + amounts.tds:
        raise MoneyError("amounts do not balance")  # unreachable by construction; defence in depth
    return amounts
