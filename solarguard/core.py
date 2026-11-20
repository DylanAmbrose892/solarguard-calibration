"""
SolarGuard LSS: calibrating a parametric low-sunlight insurance product
with 25 years of NASA satellite data.

The product (from our university group project):
  * A solar farm's monthly revenue is insured.
  * Each day, a satellite measures how much sunlight reached the site.
  * A "low-sunlight day" is a day below a site-calibrated threshold.
  * The more low days in a month, the bigger the payout tier.

This module holds the reusable logic. The notebook tells the story.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

# ---------------------------------------------------------------------------
# 1. Product parameters (taken from the SolarGuard LSS group presentation)
# ---------------------------------------------------------------------------

PLANT_MWAC = 50                 # example plant size
YIELD_MWH_PER_MWAC = 1_900      # expected energy per MWac per year
TARIFF_RM_PER_KWH = 0.17        # LSS5 average tariff, rounded

ANNUAL_REVENUE = PLANT_MWAC * YIELD_MWH_PER_MWAC * 1_000 * TARIFF_RM_PER_KWH
MONTHLY_SUM_INSURED = ANNUAL_REVENUE / 12           # about RM1.35M
ANNUAL_AGGREGATE_LIMIT = 3 * MONTHLY_SUM_INSURED    # about RM4.04M

# Payout tiers: (minimum low days in the month, share of monthly sum insured)
TIERS = [(15, 1.00), (8, 0.45), (5, 0.20)]

# The group deck priced the product with placeholder tier probabilities
DECK_TIER_PROBS = {"Tier 1 (5-7 days)": 0.06,
                   "Tier 2 (8-14 days)": 0.025,
                   "Tier 3 (15+ days)": 0.005}
PREMIUM_LOADING = 1.30          # premium = expected payout x 1.30


# ---------------------------------------------------------------------------
# 2. Getting the data
# ---------------------------------------------------------------------------

NASA_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"


def fetch_nasa_power(lat: float, lon: float, start: str, end: str,
                     cache_path: str | Path | None = None) -> pd.Series:
    """Download daily sunlight (kWh/m2/day) for one location from NASA POWER.

    The variable is ALLSKY_SFC_SW_DWN: total solar energy reaching the ground,
    clouds included. If cache_path exists, it is read instead of downloading.
    """
    if cache_path is not None and Path(cache_path).exists():
        return load_csv(cache_path)

    import requests  # imported here so the rest works without internet

    params = {
        "parameters": "ALLSKY_SFC_SW_DWN",
        "community": "RE",          # renewable energy community
        "latitude": lat,
        "longitude": lon,
        "start": start,             # YYYYMMDD
        "end": end,
        "format": "JSON",
    }
    response = requests.get(NASA_URL, params=params, timeout=120)
    response.raise_for_status()
    raw = response.json()["properties"]["parameter"]["ALLSKY_SFC_SW_DWN"]

    ghi = pd.Series(raw, dtype=float)
    ghi.index = pd.to_datetime(ghi.index, format="%Y%m%d")
    ghi = ghi.where(ghi >= 0)       # NASA marks missing days as -999
    ghi.name = "ghi_kwh_m2_day"

    if cache_path is not None:
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        ghi.to_csv(cache_path, index_label="date")
    return ghi


def load_csv(path: str | Path) -> pd.Series:
    """Read daily sunlight from a CSV.

    Accepts either the file saved by fetch_nasa_power (date, value) or a CSV
    downloaded directly from the NASA POWER website (header block, then
    YEAR, MO, DY, ALLSKY_SFC_SW_DWN columns).
    """
    text = Path(path).read_text().splitlines()
    if text and text[0].startswith("-BEGIN HEADER-"):
        skip = next(i for i, line in enumerate(text) if line.startswith("-END HEADER-")) + 1
        df = pd.read_csv(path, skiprows=skip)
        dates = pd.to_datetime(dict(year=df["YEAR"], month=df["MO"], day=df["DY"]))
        ghi = pd.Series(df["ALLSKY_SFC_SW_DWN"].to_numpy(), index=dates)
    else:
        df = pd.read_csv(path, parse_dates=["date"], index_col="date")
        ghi = df.iloc[:, 0]
    return ghi.where(ghi >= 0).rename("ghi_kwh_m2_day")


# ---------------------------------------------------------------------------
# 3. The product logic
# ---------------------------------------------------------------------------

def low_day_threshold(ghi: pd.Series, share_of_mean: float = 0.60) -> float:
    """A day is 'low' if sunlight is below this share of the long-run mean."""
    return share_of_mean * ghi.mean()


def monthly_low_days(ghi: pd.Series, threshold: float) -> pd.Series:
    """Count low-sunlight days in every calendar month."""
    is_low = (ghi < threshold).astype(int)
    return is_low.resample("MS").sum().rename("low_days")


def payout_share(low_days: int) -> float:
    """Share of the monthly sum insured paid for a given count of low days."""
    for min_days, share in TIERS:
        if low_days >= min_days:
            return share
    return 0.0


def tier_label(low_days: int) -> str:
    if low_days >= 15:
        return "Tier 3 (15+ days)"
    if low_days >= 8:
        return "Tier 2 (8-14 days)"
    if low_days >= 5:
        return "Tier 1 (5-7 days)"
    return "No payout"


def payout_history(counts: pd.Series) -> pd.DataFrame:
    """Replay the policy over history: what would each month have paid?"""
    df = counts.to_frame()
    df["tier"] = df["low_days"].map(tier_label)
    df["payout_share"] = df["low_days"].map(payout_share)
    df["payout_rm"] = df["payout_share"] * MONTHLY_SUM_INSURED
    return df


def tier_frequencies(counts: pd.Series) -> dict[str, float]:
    """Share of historical months that landed in each payout tier."""
    labels = counts.map(tier_label)
    return {t: float((labels == t).mean()) for t in DECK_TIER_PROBS}


def expected_annual_payout(tier_probs: dict[str, float]) -> float:
    """12 months x sum over tiers of (probability x payout share) x sum insured."""
    shares = {"Tier 1 (5-7 days)": 0.20,
              "Tier 2 (8-14 days)": 0.45,
              "Tier 3 (15+ days)": 1.00}
    monthly = sum(tier_probs[t] * shares[t] for t in shares)
    return 12 * monthly * MONTHLY_SUM_INSURED


def premium(expected_payout: float, loading: float = PREMIUM_LOADING) -> float:
    return expected_payout * loading


# ---------------------------------------------------------------------------
# 4. Why the days cluster (the key actuarial insight)
# ---------------------------------------------------------------------------

def binomial_tail(p_low_day: float, days: int = 30, k: int = 5) -> float:
    """P(at least k low days in a month) IF each day were independent."""
    return float(stats.binom.sf(k - 1, days, p_low_day))


def lag1_autocorrelation(ghi: pd.Series, threshold: float) -> float:
    """Correlation between 'today is low' and 'yesterday was low'."""
    is_low = (ghi < threshold).astype(float)
    return float(is_low.autocorr(lag=1))


def threshold_sensitivity(ghi: pd.Series, shares=(0.50, 0.55, 0.60, 0.65, 0.70)) -> pd.DataFrame:
    """How the price moves if the low-day threshold is set differently."""
    rows = []
    for s in shares:
        th = low_day_threshold(ghi, s)
        counts = monthly_low_days(ghi, th)
        probs = tier_frequencies(counts)
        ep = expected_annual_payout(probs)
        rows.append({
            "threshold_share_of_mean": s,
            "threshold_kwh_m2_day": round(th, 2),
            **{k: round(v * 100, 1) for k, v in probs.items()},
            "expected_payout_rm": round(ep),
            "premium_rm": round(premium(ep)),
            "premium_pct_revenue": round(premium(ep) / ANNUAL_REVENUE * 100, 2),
        })
    return pd.DataFrame(rows)
