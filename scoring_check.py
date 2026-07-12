"""
Quick manual check that scoring.py produces sensible output on real data,
before wiring the full pipeline together in main.py. Run with:
python scoring_check.py

Not part of the daily pipeline -- keep around for future debugging.
"""

import airtable_client
import indicators
import scoring

SYMBOL = "AAPL.US"

config = airtable_client.get_config()
print(f"Loaded {len(config)} config parameters from Airtable.")

history = indicators.build_symbol_history(SYMBOL, config)
print(f"Merged {len(history)} dated rows of raw indicators for {SYMBOL}.")

scored = scoring.score_series(history, config)
dropped = len(history) - len(scored)
print(f"Scored {len(scored)} dates (dropped {dropped} early dates lacking full lookback).")

columns_to_show = [
    "Date", "Price60", "Price120", "TrendScore", "TrendRegime",
    "MomentumRegime", "MomentumAdj", "ATR_Ratio", "BBW_Percentile",
    "VolatilityRegime", "VolAdj", "TechScore",
]
print("\nLast 10 scored rows:")
print(scored[columns_to_show].tail(10).to_string(index=False))
