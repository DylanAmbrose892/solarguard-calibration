import numpy as np
import pandas as pd
import pytest

from solarguard.core import (
    ANNUAL_AGGREGATE_LIMIT, ANNUAL_REVENUE, DECK_TIER_PROBS, MONTHLY_SUM_INSURED,
    binomial_tail, expected_annual_payout, monthly_low_days, payout_share, premium,
    tier_frequencies,
)


def test_sum_insured_matches_group_deck():
    assert ANNUAL_REVENUE == pytest.approx(16_150_000)
    assert MONTHLY_SUM_INSURED == pytest.approx(1_345_833, abs=1)
    assert ANNUAL_AGGREGATE_LIMIT == pytest.approx(4_037_500)


@pytest.mark.parametrize("days, share", [(0, 0), (4, 0), (5, .2), (7, .2), (8, .45), (14, .45), (15, 1), (31, 1)])
def test_payout_tiers(days, share):
    assert payout_share(days) == share


def test_deck_premium_reproduced():
    # The deck: expected payout about RM456k, premium about RM593k (3.7% of revenue)
    ep = expected_annual_payout(DECK_TIER_PROBS)
    assert ep == pytest.approx(456_000, rel=0.01)
    assert premium(ep) == pytest.approx(593_000, rel=0.01)


def test_monthly_counts():
    days = pd.date_range("2024-01-01", "2024-02-29", freq="D")
    ghi = pd.Series(5.0, index=days)
    ghi.iloc[:6] = 1.0          # 6 low days in January
    counts = monthly_low_days(ghi, threshold=3.0)
    assert counts.tolist() == [6, 0]
    freqs = tier_frequencies(counts)
    assert freqs["Tier 1 (5-7 days)"] == 0.5


def test_binomial_tail_sane():
    assert binomial_tail(0.0) == 0
    assert 0 < binomial_tail(0.05) < 0.05
