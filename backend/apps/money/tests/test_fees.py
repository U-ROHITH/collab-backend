import random

import pytest

from apps.money.fees import FeeConfig, MoneyError, compute_order_amounts, percent_of

BRAND_PAYS = FeeConfig(
    "v", commission_bps=1000, gst_bps=1800, tds_bps=100, tds_section="194-O", commission_bearer="brand"
)
CREATOR_PAYS = FeeConfig(
    "v", commission_bps=1000, gst_bps=1800, tds_bps=100, tds_section="194-O", commission_bearer="creator"
)


def test_brand_bears_commission_example() -> None:
    a = compute_order_amounts(1_000_000, BRAND_PAYS)  # ₹10,000
    assert (a.platform_fee, a.gst, a.tds) == (100_000, 18_000, 10_000)  # ₹1,000 fee, ₹180 GST on fee, 1% TDS
    assert a.brand_total == 1_118_000  # ₹11,180
    assert a.creator_payout == 990_000


def test_creator_bears_commission_example() -> None:
    a = compute_order_amounts(1_000_000, CREATOR_PAYS)
    assert a.brand_total == 1_000_000
    assert a.creator_payout == 1_000_000 - 100_000 - 18_000 - 8_820


def test_rounding_is_half_up_to_the_paisa() -> None:
    assert percent_of(5, 1000) == 1  # 0.5 -> 1
    assert percent_of(4, 1000) == 0  # 0.4 -> 0


@pytest.mark.parametrize("cfg", [BRAND_PAYS, CREATOR_PAYS])
def test_invariant_holds_for_many_amounts(cfg: FeeConfig) -> None:
    rng = random.Random(42)
    for _ in range(5000):
        a = compute_order_amounts(rng.randint(100, 1_000_000_000), cfg)
        assert a.brand_total == a.creator_payout + a.platform_fee + a.gst + a.tds
        assert min(a.platform_fee, a.gst, a.tds) >= 0 and a.creator_payout > 0


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "100"])
def test_rejects_invalid_subtotal(bad: object) -> None:
    with pytest.raises(MoneyError):
        compute_order_amounts(bad, BRAND_PAYS)  # type: ignore[arg-type]


def test_rejects_invalid_config() -> None:
    with pytest.raises(MoneyError):
        FeeConfig("v", commission_bps=20_000, gst_bps=0, tds_bps=0, tds_section="", commission_bearer="brand")
    with pytest.raises(MoneyError):
        FeeConfig("v", commission_bps=0, gst_bps=0, tds_bps=0, tds_section="", commission_bearer="nobody")


def test_fees_exceeding_value_rejected() -> None:
    cfg = FeeConfig("v", commission_bps=9000, gst_bps=1800, tds_bps=1000, tds_section="", commission_bearer="creator")
    with pytest.raises(MoneyError):
        compute_order_amounts(100, cfg)
