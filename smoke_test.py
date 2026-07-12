"""
Quick manual check that eodhd_client.py can actually reach EODHD and get
usable data back, before any Airtable or scoring logic is built on top of
it. Run with: python smoke_test.py

Not part of the daily pipeline -- keep around for future debugging (e.g.
if EODHD changes a response shape or a symbol's ticker format is wrong).
"""

import eodhd_client

SYMBOL = "AAPL.US"

print(f"Pulling EOD prices for {SYMBOL}...")
eod_rows = eodhd_client.get_eod(SYMBOL)
print(f"  {len(eod_rows)} rows returned")
print(f"  Most recent row: {eod_rows[-1]}")

print(f"\nPulling SMA(60) for {SYMBOL}...")
sma_rows = eodhd_client.get_technical(SYMBOL, "sma", period=60)
print(f"  {len(sma_rows)} rows returned")
print(f"  Most recent row: {sma_rows[-1]}")

print("\nSmoke test passed: EODHD is reachable and returning data.")
