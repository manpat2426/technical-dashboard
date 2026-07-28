"""
Central configuration for the technical dashboard pipeline.

Loads secrets from a local .env file (via python-dotenv) and defines
structural constants: which Airtable base/tables to talk to, which EODHD
endpoints to call, and the test-mode symbol list.

Model parameters (MA periods, thresholds, etc.) are deliberately NOT
hardcoded here -- they live in the Airtable Config table and are read at
runtime by airtable_client.py, so the methodology stays centralized and
editable from Airtable rather than buried in code.
"""

import os
from dotenv import load_dotenv

load_dotenv()

EODHD_API_KEY = os.environ.get("EODHD_API_KEY")
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN")

if not EODHD_API_KEY:
    raise RuntimeError(
        "EODHD_API_KEY is not set. Copy .env.example to .env and fill in your key."
    )
if not AIRTABLE_TOKEN:
    raise RuntimeError(
        "AIRTABLE_TOKEN is not set. Copy .env.example to .env and fill in your key."
    )

# --- Airtable base and tables ---
AIRTABLE_BASE_ID = "appummriRwPGUNjsj"
TABLE_CONFIG = "tbl9RXltHV5PznVCK"
TABLE_SYMBOLS = "tbluKC86ay1ab08MD"
TABLE_INDICATORS = "tbloUYFRODxmP5fhh"
TABLE_SCORES_CURRENT = "tblIb8EndSxFnkZhF"

# --- EODHD API ---
EODHD_TECHNICAL_URL = "https://eodhd.com/api/technical/{symbol}"
EODHD_EOD_URL = "https://eodhd.com/api/eod/{symbol}"

# Each technical request costs 5 EODHD API credits, out of a 100,000/day
# budget and an assumed conservative ~1,000/minute cap. Worst case, every
# request costs 5 credits, so pausing 0.4s between requests caps us at
# ~150 requests/minute from the pause alone (750 credits/min, 75% of the
# assumed cap) -- and real network latency on top of the pause pulls
# actual throughput down further, adding more margin. Still comfortably
# safe for an unattended run, without being needlessly slow.
EODHD_REQUEST_PAUSE_SECONDS = 0.4

# --- Test mode ---
# Flip back to True (with a short TEST_SYMBOLS list) if you need to
# isolate a small batch again for debugging.
TEST_MODE = False
TEST_SYMBOLS = ["AAPL.US", "MSFT.US", "TLT.US", "XLU.US", "VWO.US"]

# --- History window ---
# How much daily history to pull per symbol from EODHD in a single call.
# This must comfortably exceed the longest checkpoint lookback used by
# Scores_Current (3 months) plus the longest lookback used by any derived
# metric (MA120, ATR90Avg, BBW_Percentile's 125-period window), so every
# checkpoint date has a full indicator lookback behind it.
HISTORY_YEARS = 2
