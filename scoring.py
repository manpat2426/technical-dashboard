"""
The technical scoring model: trend, momentum, and volatility regime
classification, and the score math that combines them into TechScore.

This is the heart of the pipeline, so every rule here is written to be
traceable line-by-line back to the methodology in the build brief. Every
numeric threshold and lookback period is read from `config` (the
Parameter -> Value dict loaded from the Airtable Config table) rather
than hardcoded, so the methodology stays centrally editable in Airtable.
Config parameter names below match the Config table's `Parameter` column
exactly, e.g. config["PRICE60_HIGH"], config["ATR_Baseline_Period"].

Regime classification (_trend_regime, _momentum_regime,
_volatility_regime) is evaluated TOP-DOWN: the first matching condition
wins and the rest are never checked, exactly as the brief specifies. The
order of the if/elif checks in each function is the order given in the
brief -- don't reorder them without re-checking the brief.
"""

import pandas as pd


# ============================================================
# Derived series -- need the full date-ordered history per symbol,
# not just a single date, because these are rolling calculations.
# ============================================================

def compute_derived_series(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Adds the columns the script computes itself (per the brief, NOT
    pulled directly from EODHD): ATR90Avg, ROC20, BBW, BBW_Percentile,
    ATR_Ratio.

    df must be sorted ascending by Date and already contain: Close,
    ATR20, BBUpper, BBMid, BBLower (see indicators.build_symbol_history).
    """
    df = df.copy()

    atr_baseline_period = int(config["ATR_Baseline_Period"])  # 90
    roc_period = int(config["ROC_Period"])                     # 20
    bbw_lookback = int(config["BB_BBW_Period"])                # 125

    # ATR90Avg: trailing average of the ATR20 series itself (a baseline
    # for "is volatility elevated relative to its own recent norm").
    df["ATR90Avg"] = df["ATR20"].rolling(window=atr_baseline_period).mean()

    # ROC20: adjusted_close vs. adjusted_close N periods ago. Per the
    # brief this is computed here from the Close series, NOT via EODHD's
    # own roc technical function.
    df["ROC20"] = df["Close"] / df["Close"].shift(roc_period) - 1

    # BBW: Bollinger Band width normalized by the mid band -- a
    # volatility/"tightness of the bands" measure.
    df["BBW"] = (df["BBUpper"] - df["BBLower"]) / df["BBMid"]

    # BBW_Percentile: where today's BBW ranks within its own trailing
    # window (0.0 = tightest bands seen in the window, 1.0 = widest).
    # rolling(...).rank(method="max", pct=True) ranks each window's last
    # value against the rest of that window and returns it as a fraction
    # -- with method="max", ties take the highest rank, which makes this
    # exactly equal to (window <= current_value).mean(), the inclusive
    # definition we want, but computed by pandas' compiled rolling-rank
    # implementation instead of a per-row Python callback. On the full
    # 113-symbol universe the old .rolling().apply(..., raw=False) version
    # was the dominant cost of the whole pipeline (tens of thousands of
    # Python-level calls per symbol); this is the vectorized equivalent.
    df["BBW_Percentile"] = df["BBW"].rolling(window=bbw_lookback).rank(method="max", pct=True)

    # ATR_Ratio: today's ATR20 relative to its own 90-day baseline --
    # >1 means recent volatility is running hotter than normal.
    df["ATR_Ratio"] = df["ATR20"] / df["ATR90Avg"]

    return df


# ============================================================
# Trend
# ============================================================

def _trend60_score(price60: float, config: dict) -> int:
    """Trend60Score: 3 if Price60>=PRICE60_HIGH, 1 if >=1.00,
    -1 if >=PRICE60_LOW, else -3."""
    if price60 >= config["PRICE60_HIGH"]:
        return 3
    if price60 >= 1.00:
        return 1
    if price60 >= config["PRICE60_LOW"]:
        return -1
    return -3


def _trend120_score(price120: float, config: dict) -> int:
    """Trend120Score: 3 if Price120>=PRICE120_HIGH, 1 if >=1.00,
    -1 if >=PRICE120_LOW, else -3."""
    if price120 >= config["PRICE120_HIGH"]:
        return 3
    if price120 >= 1.00:
        return 1
    if price120 >= config["PRICE120_LOW"]:
        return -1
    return -3


def _trend_regime(price60: float, price120: float, ma_align: int, config: dict) -> str:
    """Plain-language trend state. Top-down, first match wins -- order
    matches the brief exactly: Strong Uptrend, Uptrend, Early Uptrend /
    Recovery, Early Downtrend / Roll Over, Strong Downtrend, Downtrend,
    else Neutral / Transition."""
    price60_high = config["PRICE60_HIGH"]
    price60_low = config["PRICE60_LOW"]
    price120_high = config["PRICE120_HIGH"]
    price120_low = config["PRICE120_LOW"]

    if price60 >= price60_high and price120 >= price120_high and ma_align == 1:
        return "Strong Uptrend"
    if price60 >= 1.00 and price120 >= 1.00:
        return "Uptrend"
    if price60 >= 1.00 and price120 < 1.00:
        return "Early Uptrend / Recovery"
    if price60 < 1.00 and price120 >= 1.00:
        return "Early Downtrend / Roll Over"
    if price60 < price60_low and price120 < price120_low:
        return "Strong Downtrend"
    if price60 < 1.00 and price120 < 1.00:
        return "Downtrend"
    return "Neutral / Transition"


# ============================================================
# Momentum
# ============================================================

# MomentumAdj mapping, per the brief. Improving/Deteriorating are listed
# here for completeness even though _momentum_regime() below can't
# produce them yet (they need a prior-period comparison the brief defers
# to a later iteration) -- anything not matching a rule falls through to
# Neutral instead.
MOMENTUM_ADJ_BY_REGIME = {
    "Accelerating Bullish": 3,
    "Bullish": 2,
    "Improving": 1,
    "Neutral": 0,
    "Deteriorating": -1,
    "Bearish": -2,
    "Accelerating Bearish": -3,
}


def _momentum_regime(macd: float, macd_signal: float, rsi: float, roc: float) -> str:
    """Plain-language momentum state. Top-down, first match wins -- order
    matches the brief: Accelerating Bullish, Accelerating Bearish,
    Bullish, Bearish, else Neutral."""
    if macd > macd_signal and macd > 0 and rsi >= 60 and roc > 0:
        return "Accelerating Bullish"
    if macd < macd_signal and macd < 0 and rsi <= 40 and roc < 0:
        return "Accelerating Bearish"
    if macd > macd_signal and rsi >= 50 and roc >= 0:
        return "Bullish"
    if macd < macd_signal and rsi < 50 and roc <= 0:
        return "Bearish"
    return "Neutral"


# ============================================================
# Volatility
# ============================================================

VOL_ADJ_BY_REGIME = {
    "Compressed / Squeeze": 2,
    "Orderly": 1,
    "Expanding": 0,
    "Elevated": -1,
    "Extreme / Unstable": -2,
}


def _volatility_regime(atr_ratio: float, bbw_percentile: float, config: dict) -> str:
    """Plain-language volatility state. Top-down, first match wins --
    order matches the brief: Extreme / Unstable, Elevated,
    Compressed / Squeeze, Expanding, else Orderly."""
    atr_high = config["ATR_HIGH"]
    atr_low = config["ATR_LOW"]
    atr_normal_high = config["ATR_NORMAL_HIGH"]
    bbw_wide_high = config["BBW_WIDE_HIGH_X"]
    bbw_squeeze_low = config["BBW_SQUEEZE_LOW_X"]

    if atr_ratio >= atr_high and bbw_percentile > bbw_wide_high:
        return "Extreme / Unstable"
    if atr_ratio >= atr_high:
        return "Elevated"
    if atr_ratio <= atr_low and bbw_percentile <= bbw_squeeze_low:
        return "Compressed / Squeeze"
    if atr_ratio > atr_normal_high or bbw_percentile > bbw_wide_high:
        return "Expanding"
    return "Orderly"


# ============================================================
# Putting it together
# ============================================================

def score_row(row: dict, config: dict) -> dict:
    """
    Computes every scored/derived column for one date, given that date's
    inputs (Close, MA60, MA120, ATR_Ratio, BBW, BBW_Percentile, MACD,
    MACD_Signal, RSI14, ROC20) and the live Config values.

    Returns a dict of exactly the columns Scores_Current / Scores_History
    need, ready to hand to airtable_client.
    """
    close = row["Close"]
    ma60 = row["MA60"]
    ma120 = row["MA120"]

    price60 = close / ma60
    price120 = close / ma120
    ma_align = 1 if abs(ma60 / ma120 - 1) <= config["MA_ALIGN_TOL"] else 0

    trend60_score = _trend60_score(price60, config)
    trend120_score = _trend120_score(price120, config)
    ma_align_score = 1 if ma_align == 1 else 0
    trend_score = trend60_score + trend120_score + ma_align_score
    trend_regime = _trend_regime(price60, price120, ma_align, config)

    momentum_regime = _momentum_regime(row["MACD"], row["MACD_Signal"], row["RSI14"], row["ROC20"])
    momentum_adj = MOMENTUM_ADJ_BY_REGIME[momentum_regime]

    volatility_regime = _volatility_regime(row["ATR_Ratio"], row["BBW_Percentile"], config)
    vol_adj = VOL_ADJ_BY_REGIME[volatility_regime]

    tech_score = trend_score + momentum_adj + vol_adj

    return {
        "Price60": price60,
        "Price120": price120,
        "MAAlign": ma_align,
        "Trend60Score": trend60_score,
        "Trend120Score": trend120_score,
        "MAAlignScore": ma_align_score,
        "TrendScore": trend_score,
        "TrendRegime": trend_regime,
        "MomentumRegime": momentum_regime,
        "MomentumAdj": momentum_adj,
        "ATR_Ratio": row["ATR_Ratio"],
        "BBW": row["BBW"],
        "BBW_Percentile": row["BBW_Percentile"],
        "VolatilityRegime": volatility_regime,
        "VolAdj": vol_adj,
        "TechScore": tech_score,
    }


# Columns that must be non-null for a date to be scoreable. Early dates
# in a symbol's history won't have these yet (not enough lookback for
# MA120 / ATR90Avg / BBW_Percentile's 125-period window) -- those dates
# are dropped rather than scored on incomplete data.
_REQUIRED_COLUMNS_FOR_SCORING = [
    "Close", "MA60", "MA120", "ATR20", "ATR90Avg", "ATR_Ratio",
    "BBUpper", "BBMid", "BBLower", "BBW", "BBW_Percentile",
    "MACD", "MACD_Signal", "RSI14", "ROC20",
]


def score_series(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    Runs the full model across every date in df that has a complete
    lookback. df must already have Symbol and Date columns (see
    indicators.build_symbol_history) plus the raw columns
    compute_derived_series needs.

    Returns a DataFrame with every raw/derived input column (Symbol, Date,
    Close, MA60, MA120, ATR20, ATR90Avg, BBUpper/Mid/Lower, MACD,
    MACD_Signal, RSI14, ROC20, BBW, BBW_Percentile, ATR_Ratio) PLUS every
    scored/regime column (Price60, TrendScore, TrendRegime, etc.), one row
    per scoreable date. Carrying the raw/derived inputs through as well
    (not just the scores) is what lets main.py populate Indicators'
    "carried in for traceability" columns without recomputing anything.
    """
    df = compute_derived_series(df, config)
    scoreable = df.dropna(subset=_REQUIRED_COLUMNS_FOR_SCORING).reset_index(drop=True)

    scored_rows = [score_row(row, config) for row in scoreable.to_dict("records")]
    scored = pd.DataFrame(scored_rows, index=scoreable.index)

    # scoreable already has ATR_Ratio/BBW/BBW_Percentile (added by
    # compute_derived_series); score_row() returns them again so it stays
    # self-contained when called on its own. Drop the duplicates here
    # rather than overwriting scoreable's columns with identical values.
    scored = scored.drop(columns=["ATR_Ratio", "BBW", "BBW_Percentile"])
    return pd.concat([scoreable, scored], axis=1)
