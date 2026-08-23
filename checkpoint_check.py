"""
Quick manual check that the historical checkpoint retrieval (1W/2W/3W/
1M/2M/3M) is picking the right "nearest snapshot on or before" rows.
For each TEST_SYMBOLS symbol, shows the anchor (latest) row, each target
date, and the actual retrieved row's own Date/TechScore/regimes side by
side, so you can eyeball that retrieved Date <= target Date and is the
closest available one.

Run with: python checkpoint_check.py
Not part of the daily pipeline -- keep around for future debugging.
"""

import pandas as pd

import airtable_client
import indicators
import scoring
from config import TEST_SYMBOLS
from main import _CHECKPOINT_MONTH_OFFSETS, _CHECKPOINT_WEEK_OFFSETS, _nearest_snapshot_on_or_before

pd.set_option("display.width", 160)

config = airtable_client.get_config()

for symbol in TEST_SYMBOLS:
    history = indicators.build_symbol_history(symbol, config)
    scored = scoring.score_series(history, config)
    latest = scored.iloc[-1]
    anchor = pd.to_datetime(latest["Date"])
    dates_dt = pd.to_datetime(scored["Date"])

    print(f"\n{symbol}: anchor Date={latest['Date']}  TechScore={latest['TechScore']}  "
          f"TrendRegime={latest['TrendRegime']}  MomentumRegime={latest['MomentumRegime']}  "
          f"VolatilityRegime={latest['VolatilityRegime']}")

    rows = []
    for suffix, delta in _CHECKPOINT_WEEK_OFFSETS:
        target = anchor - delta
        row = _nearest_snapshot_on_or_before(scored, dates_dt, target)
        rows.append((suffix, target, row))
    for suffix, months in _CHECKPOINT_MONTH_OFFSETS:
        target = anchor - pd.DateOffset(months=months)
        row = _nearest_snapshot_on_or_before(scored, dates_dt, target)
        rows.append((suffix, target, row))

    for suffix, target, row in rows:
        if row is None:
            print(f"  {suffix:>2}: target={target.date()}  -> no snapshot available (blank)")
        else:
            gap = (target.normalize() - pd.to_datetime(row["Date"])).days
            print(f"  {suffix:>2}: target={target.date()}  -> retrieved Date={row['Date']} "
                  f"(gap {gap}d)  TechScore={row['TechScore']}  TrendRegime={row['TrendRegime']}  "
                  f"MomentumRegime={row['MomentumRegime']}  VolatilityRegime={row['VolatilityRegime']}")
