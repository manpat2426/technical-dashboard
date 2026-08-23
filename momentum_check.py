"""
Quick manual check that the Improving/Deteriorating momentum regimes are
firing on real turns, not noise. Scans the TEST_SYMBOLS for dates where
MomentumRegime came out Improving or Deteriorating, and prints the
before/after (~5 trading days apart) input values that drove the call.

Run with: python momentum_check.py
Not part of the daily pipeline -- keep around for future debugging.
"""

import pandas as pd

import airtable_client
import indicators
import scoring
from config import TEST_SYMBOLS

pd.set_option("display.width", 160)

config = airtable_client.get_config()

columns_to_show = [
    "Date", "MACD", "MACD_prior", "MACD_Signal", "MACD_Signal_prior",
    "RSI14", "RSI14_prior", "ROC20", "ROC20_prior", "MomentumRegime",
]

for symbol in TEST_SYMBOLS:
    history = indicators.build_symbol_history(symbol, config)
    scored = scoring.score_series(history, config)

    turns = scored[scored["MomentumRegime"].isin(["Improving", "Deteriorating"])]
    print(f"\n{symbol}: {len(turns)} Improving/Deteriorating date(s) out of {len(scored)} scored.")
    if not turns.empty:
        print(turns[columns_to_show].to_string(index=False))
