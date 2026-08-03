"""
The decision layer: turns the daily TechScore into one of six stable,
actionable states.

This sits strictly ON TOP of the scoring model -- it reads TechScore and
nothing else, and it does not change how TechScore, the trend/momentum/
volatility scores, or any regime is calculated. scoring.py is untouched
by anything in here.

Why it exists: TechScore is built from hard threshold cutoffs (Price60
crossing 1.05, RSI crossing 60, and so on), so it can jump a point or two
day-to-day on a move that means nothing. The decision layer smooths that
away and classifies each security on two dimensions -- the LEVEL of the
smoothed score (sTech) and its SLOPE (trajectory).

The governing principle: slope's authority to change the decision is
level-dependent. A security in the High band has earned patience, so only
a big, month-scale, twice-confirmed fade downgrades it; a security in the
Middle or Low band gets read week-over-week and reacts fast.

Three things make a decision stateful rather than a pure function of
today's numbers, which is why everything below runs as a forward pass
(oldest date -> newest, per symbol) carrying state:
  1. sTech is a trailing rolling mean, so it needs the prior 15 days.
  2. Band assignment has hysteresis, so it needs yesterday's band.
  3. A new state has to hold DECISION_PERSIST_DAYS days before it becomes
     official, so it needs the pending-state counter.
Every date's Decision is therefore the honest point-in-time value -- what
this tool would have said on that date using only data up to it. Today's
decision is never applied backward over history.
"""

import numpy as np
import pandas as pd


# ============================================================
# DECISION LAYER -- TUNABLE PARAMETERS
# All construction-based (absolute), not distribution-fitted.
# ============================================================
SMOOTHING_WINDOW        = 15    # trading days; sTech = rolling mean of TechScore
HIGH_BAND_CUTOFF        = 5     # sTech >= this  -> High band
LOW_BAND_CUTOFF         = -1    # sTech <  this  -> Low band; between = Middle
BAND_HYSTERESIS_BUFFER  = 1.5   # must clear a band edge by this much to exit that band
HIGH_BAND_FADE_DROP     = 3     # sTech drop over ~1M (confirmed by 2M) -> Concerning
MID_BAND_SLOPE          = 2     # sTech WoW move magnitude -> Improving/Concerning
LOW_BAND_EXIT_DROP      = 1.5   # further WoW drop while in Low band -> Exit
DECISION_PERSIST_DAYS   = 2     # a new state must hold this many consecutive days
# ============================================================


# The six decision states. These strings are the exact single-select
# option names on Scores_Current's Decision / Decision_1W / _2W / _3W /
# _1M fields -- change one here and the matching Airtable option has to
# be renamed too, or the write will be rejected.
STATE_IMPROVING = "Improving"
STATE_STEADY_CONSTRUCTIVE = "Steady-constructive"
STATE_NEUTRAL = "Neutral / No Signal"
STATE_CONCERNING = "Concerning"
STATE_STEADY_WEAK = "Steady-weak"
STATE_EXIT = "Exit"

DECISION_STATES = [
    STATE_IMPROVING,
    STATE_STEADY_CONSTRUCTIVE,
    STATE_NEUTRAL,
    STATE_CONCERNING,
    STATE_STEADY_WEAK,
    STATE_EXIT,
]

# Level bands. Internal only -- not written anywhere, just carried
# through the forward pass so hysteresis has yesterday's value.
BAND_HIGH = "High"
BAND_MIDDLE = "Middle"
BAND_LOW = "Low"

# Slope spacing, per band. High band reads month-scale (now vs ~1M vs
# ~2M); Middle and Low read week-over-week (now vs 1W vs 2W vs 3W). Same
# calendar offsets the Scores_Current checkpoint columns use -- weeks as
# plain day counts, months as calendar-month subtraction.
_HIGH_BAND_OFFSETS = [pd.DateOffset(months=1), pd.DateOffset(months=2)]
_WEEK_BAND_OFFSETS = [pd.Timedelta(days=7), pd.Timedelta(days=14), pd.Timedelta(days=21)]


# ============================================================
# Nearest-prior-date lookback
# ============================================================

def _lagged(values: np.ndarray, dates: pd.Series, offset) -> np.ndarray:
    """
    For every row, the value of `values` at the nearest available date on
    or before (that row's date - offset) -- NaN where no such row exists.

    Same retrieval rule as main._nearest_snapshot_on_or_before (the
    checkpoint columns): take the latest snapshot at or before the target,
    because trading calendars have holes and an exact-date match would
    leave gaps. Expressed here as a vectorized positional lookup
    (searchsorted over the already-ascending date column) rather than a
    per-row DataFrame slice, since the forward pass needs 5 of these per
    date across ~2 years x 113 symbols. `side="right" - 1` is exactly
    "index of the last date <= target"; -1 means the target predates the
    symbol's earliest scoreable date.
    """
    targets = (dates - offset).values
    idx = np.searchsorted(dates.values, targets, side="right") - 1
    return np.where(idx >= 0, values[idx.clip(min=0)], np.nan)


# ============================================================
# Slope
# ============================================================

def _slope_direction(steps) -> str:
    """
    "up", "down", or "flat" from a sequence of consecutive backward steps
    (now-vs-prior, prior-vs-prior-prior, ...).

    ALL steps must agree for a direction to be called -- one leg
    disagreeing means the move isn't a trajectory, it's a wobble, and the
    security reads flat. A NaN step (not enough history for that lookback
    yet) is also flat, never a direction: an unconfirmable slope must not
    be able to move the decision.
    """
    if any(pd.isna(step) for step in steps):
        return "flat"
    if all(step > 0 for step in steps):
        return "up"
    if all(step < 0 for step in steps):
        return "down"
    return "flat"


# ============================================================
# Bands (with boundary hysteresis)
# ============================================================

def _band(prev_band: str | None, stech: float) -> str:
    """
    Today's level band, given yesterday's. Entry uses the plain cutoffs;
    EXIT requires clearing the edge by BAND_HYSTERESIS_BUFFER, so a score
    hovering on a boundary doesn't flip the band (and with it the whole
    slope-spacing rule) back and forth day after day. Inside the buffer
    zone the prior band is simply carried forward.
    """
    if prev_band == BAND_HIGH and stech >= HIGH_BAND_CUTOFF - BAND_HYSTERESIS_BUFFER:
        return BAND_HIGH
    if prev_band == BAND_LOW and stech <= LOW_BAND_CUTOFF + BAND_HYSTERESIS_BUFFER:
        return BAND_LOW
    if stech >= HIGH_BAND_CUTOFF:
        return BAND_HIGH
    if stech < LOW_BAND_CUTOFF:
        return BAND_LOW
    return BAND_MIDDLE


# ============================================================
# Classification -- first match wins within each band
# ============================================================

def _classify_high(stech: float, stech_1m: float, stech_2m: float) -> str:
    """High band: patient. Only a month-scale fade that the 2M leg also
    confirms is enough to downgrade; everything else is constructive."""
    direction = _slope_direction((stech - stech_1m, stech_1m - stech_2m))
    if direction == "down" and (stech_1m - stech) >= HIGH_BAND_FADE_DROP:
        return STATE_CONCERNING
    return STATE_STEADY_CONSTRUCTIVE


def _classify_middle(stech: float, stech_1w: float, stech_2w: float, stech_3w: float) -> str:
    """Middle band: reactive, week-over-week. Directional states are
    checked first; Neutral is the residual, not a positive finding."""
    direction = _slope_direction((stech - stech_1w, stech_1w - stech_2w, stech_2w - stech_3w))
    move = abs(stech - stech_1w)
    if direction == "up" and move >= MID_BAND_SLOPE:
        return STATE_IMPROVING
    if direction == "down" and move >= MID_BAND_SLOPE:
        return STATE_CONCERNING
    return STATE_NEUTRAL


def _classify_low(stech: float, stech_1w: float, stech_2w: float, stech_3w: float) -> str:
    """Low band: reactive, week-over-week, and quicker to say Exit than
    the Middle band is to say Concerning (LOW_BAND_EXIT_DROP <
    MID_BAND_SLOPE) -- already-weak securities get less patience. A real
    turn up still outranks a further drop, so Improving is checked first."""
    direction = _slope_direction((stech - stech_1w, stech_1w - stech_2w, stech_2w - stech_3w))
    move = abs(stech - stech_1w)
    if direction == "up" and move >= MID_BAND_SLOPE:
        return STATE_IMPROVING
    if direction == "down" and move >= LOW_BAND_EXIT_DROP:
        return STATE_EXIT
    return STATE_STEADY_WEAK


# ============================================================
# The stateful forward pass
# ============================================================

def add_decision_series(scored: pd.DataFrame) -> pd.DataFrame:
    """
    Adds two columns to a scored DataFrame (see scoring.score_series):

      sTech    -- trailing rolling mean of TechScore over SMOOTHING_WINDOW
                  trading days. Every level and slope reading uses this,
                  never raw TechScore.
      Decision -- the official decision state as of that date, one of
                  DECISION_STATES, or None for dates too early to have a
                  full smoothing window behind them.

    `scored` must be one symbol's history, ascending by Date (which is
    what score_series returns). Runs oldest -> newest carrying band,
    official decision, and the pending-state counter, so each date's
    Decision only ever depends on data up to that date.

    Bootstrap detail: the first date that produces a state has no prior
    official decision to carry forward, so it is adopted immediately
    rather than waiting out DECISION_PERSIST_DAYS -- the persistence rule
    exists to damp CHANGES, and there's nothing to change from yet.
    """
    scored = scored.copy()
    if scored.empty:
        scored["sTech"] = pd.Series(dtype="float64")
        scored["Decision"] = pd.Series(dtype="object")
        return scored

    stech = scored["TechScore"].rolling(window=SMOOTHING_WINDOW).mean()
    scored["sTech"] = stech

    stech_values = stech.to_numpy(dtype="float64")
    dates = pd.to_datetime(scored["Date"])

    # Every lookback the forward pass needs, computed up front in one
    # vectorized pass each instead of searching the date column per row.
    stech_1m, stech_2m = (_lagged(stech_values, dates, offset) for offset in _HIGH_BAND_OFFSETS)
    stech_1w, stech_2w, stech_3w = (_lagged(stech_values, dates, offset) for offset in _WEEK_BAND_OFFSETS)

    band = None
    official = None
    pending = None
    pending_days = 0
    decisions = []

    for i in range(len(scored)):
        current = stech_values[i]

        # Not enough history for a smoothed level yet -- no band, no
        # slope, no decision. Recorded as None rather than guessed.
        if np.isnan(current):
            decisions.append(None)
            continue

        band = _band(band, current)

        if band == BAND_HIGH:
            candidate = _classify_high(current, stech_1m[i], stech_2m[i])
        elif band == BAND_LOW:
            candidate = _classify_low(current, stech_1w[i], stech_2w[i], stech_3w[i])
        else:
            candidate = _classify_middle(current, stech_1w[i], stech_2w[i], stech_3w[i])

        # Decision-transition persistence: a newly-computed state only
        # becomes official after holding DECISION_PERSIST_DAYS consecutive
        # days. Until then the prior official decision stands.
        if official is None:
            official = candidate
            pending, pending_days = None, 0
        elif candidate == official:
            pending, pending_days = None, 0
        else:
            pending_days = pending_days + 1 if candidate == pending else 1
            pending = candidate
            if pending_days >= DECISION_PERSIST_DAYS:
                official = candidate
                pending, pending_days = None, 0

        decisions.append(official)

    scored["Decision"] = decisions
    return scored
