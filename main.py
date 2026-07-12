"""
Orchestrates the full pipeline: for each symbol, pull + merge raw
indicators (indicators.py), score every date (scoring.py), then write
the latest values to Indicators/Scores_Current and the trailing backfill
window to Scores_History (airtable_client.py).

Safety gate: running with no arguments only COMPUTES everything and
prints a summary of what would be written -- it never touches Airtable.
Pass --write to actually perform the Airtable writes, after reviewing
the summary.

    python main.py            # dry run: pull, score, print summary only
    python main.py --write    # dry run summary, then actually write

Currently gated by config.TEST_MODE / config.TEST_SYMBOLS -- see
config.py to switch to the full Symbols table once the test batch is
verified in Airtable.
"""

import argparse
import sys

import pandas as pd

import airtable_client
import indicators
import scoring
from config import TEST_MODE, TEST_SYMBOLS, BACKFILL_MONTHS


def _to_native(fields: dict) -> dict:
    """Airtable's API rejects NaN/NaT and numpy scalar types. Converts
    pandas' missing-value markers to None (Airtable treats that as 'leave
    blank') and unwraps numpy types (e.g. numpy.float64) to plain Python
    types so the JSON encoder used by `requests` can serialize them."""
    clean = {}
    for key, value in fields.items():
        if pd.isna(value):
            value = None
        elif hasattr(value, "item"):
            value = value.item()
        clean[key] = value
    return clean


def build_indicators_row(latest: pd.Series) -> dict:
    """One symbol's latest raw+derived indicator values, for Indicators."""
    return _to_native({
        "Symbol": latest["Symbol"],
        "Date": latest["Date"],
        "Close": latest["Close"],
        "MA60": latest["MA60"],
        "MA120": latest["MA120"],
        "ATR20": latest["ATR20"],
        "ATR90Avg": latest["ATR90Avg"],
        "BBUpper": latest["BBUpper"],
        "BBLower": latest["BBLower"],
        "BBMid": latest["BBMid"],
        "MACD": latest["MACD"],
        "MACD_Signal": latest["MACD_Signal"],
        "RSI14": latest["RSI14"],
        "ROC20": latest["ROC20"],
    })


def build_scores_current_row(latest: pd.Series, group: str) -> dict:
    """One symbol's latest computed scores, for Scores_Current."""
    return _to_native({
        "Symbol": latest["Symbol"],
        "Group": group,
        "Date": latest["Date"],
        "Price60": latest["Price60"],
        "Price120": latest["Price120"],
        "MAAlign": latest["MAAlign"],
        "Trend60Score": latest["Trend60Score"],
        "Trend120Score": latest["Trend120Score"],
        "MAAlignScore": latest["MAAlignScore"],
        "TrendScore": latest["TrendScore"],
        "TrendRegime": latest["TrendRegime"],
        "MACD": latest["MACD"],
        "MACD_Signal": latest["MACD_Signal"],
        "RSI14": latest["RSI14"],
        "ROC20": latest["ROC20"],
        "MomentumRegime": latest["MomentumRegime"],
        "MomentumAdj": latest["MomentumAdj"],
        "ATR_Ratio": latest["ATR_Ratio"],
        "BBW": latest["BBW"],
        "BBW_Percentile": latest["BBW_Percentile"],
        "VolatilityRegime": latest["VolatilityRegime"],
        "VolAdj": latest["VolAdj"],
        "TechScore": latest["TechScore"],
    })


def build_scores_history_rows(window: pd.DataFrame, group: str, snapshot_type: str) -> list:
    """One row per backfilled date, for Scores_History. `window` should
    already be limited to the backfill period before calling this."""
    rows = []
    for record in window.to_dict("records"):
        rows.append(_to_native({
            "Symbol": record["Symbol"],
            "Group": group,
            "Date": record["Date"],
            "TrendScore": record["TrendScore"],
            "MomentumAdj": record["MomentumAdj"],
            "VolAdj": record["VolAdj"],
            "TechScore": record["TechScore"],
            "TrendRegime": record["TrendRegime"],
            "MomentumRegime": record["MomentumRegime"],
            "VolatilityRegime": record["VolatilityRegime"],
            "SnapshotType": snapshot_type,
        }))
    return rows


def backfill_window(scored: pd.DataFrame, months: int) -> pd.DataFrame:
    """Rows within the trailing `months` of the most recent scored date.
    `scored` is already limited to fully-lookback dates by
    scoring.score_series, so every row returned here is safe to
    snapshot -- no date here was computed on a partial lookback window."""
    dates = pd.to_datetime(scored["Date"])
    cutoff = dates.max() - pd.DateOffset(months=months)
    return scored[dates >= cutoff].reset_index(drop=True)


def run(symbols_to_process: list, write: bool):
    config = airtable_client.get_config()
    print(f"Loaded {len(config)} config parameters from Airtable.")

    active_symbols = {s["Symbol"]: s for s in airtable_client.get_active_symbols()}

    indicators_rows = []
    scores_current_rows = []
    scores_history_rows = []
    skipped = []
    errored = []

    for symbol in symbols_to_process:
        symbol_info = active_symbols.get(symbol)
        if symbol_info is None:
            print(f"\n{symbol}: not found (or not Active) in the Symbols table -- skipping.")
            skipped.append(symbol)
            continue
        group = symbol_info.get("Group") or ""

        print(f"\n{symbol}: pulling from EODHD and scoring...")
        try:
            history = indicators.build_symbol_history(symbol, config)
            scored = scoring.score_series(history, config)
        except Exception as exc:
            # One bad ticker (e.g. a format EODHD doesn't recognize)
            # shouldn't take down the whole run -- log it and move on.
            print(f"  ERROR: {exc}")
            errored.append((symbol, str(exc)))
            continue

        if scored.empty:
            print(f"  WARNING: no scoreable dates for {symbol} (insufficient history) -- skipping.")
            skipped.append(symbol)
            continue

        latest = scored.iloc[-1]
        indicators_rows.append(build_indicators_row(latest))
        scores_current_rows.append(build_scores_current_row(latest, group))

        window = backfill_window(scored, BACKFILL_MONTHS)
        scores_history_rows.extend(build_scores_history_rows(window, group, "Backfill"))
        print(f"  {len(scored)} scoreable dates total; backfilling {len(window)} snapshots "
              f"(latest: {latest['Date']}, TechScore={latest['TechScore']}).")

    print("\n" + "=" * 60)
    print("WRITE SUMMARY")
    print("=" * 60)
    print(f"Symbols requested : {len(symbols_to_process)}")
    if skipped:
        print(f"Symbols skipped (not Active / no scoreable data): {skipped}")
    if errored:
        print(f"Symbols ERRORED (EODHD pull failed): {[s for s, _ in errored]}")
        for symbol, message in errored:
            print(f"  {symbol}: {message}")
    print(f"Indicators        : {len(indicators_rows)} row(s), 1 per symbol "
          f"-> {[r['Symbol'] for r in indicators_rows]}")
    print(f"Scores_Current    : {len(scores_current_rows)} row(s), 1 per symbol "
          f"-> {[r['Symbol'] for r in scores_current_rows]}")
    print(f"Scores_History    : {len(scores_history_rows)} row(s) total "
          f"(SnapshotType=Backfill, upserted by Symbol+Date)")
    for symbol in symbols_to_process:
        count = sum(1 for r in scores_history_rows if r["Symbol"] == symbol)
        if count:
            print(f"  {symbol}: {count} snapshot rows")

    if not write:
        print("\nDry run only -- nothing written to Airtable. Re-run with --write to apply.")
    else:
        print("\nWriting to Airtable...")
        airtable_client.upsert_indicators(indicators_rows)
        print(f"  Indicators: wrote {len(indicators_rows)} row(s).")
        airtable_client.upsert_scores_current(scores_current_rows)
        print(f"  Scores_Current: wrote {len(scores_current_rows)} row(s).")
        airtable_client.upsert_scores_history(scores_history_rows, symbols_to_process)
        print(f"  Scores_History: wrote {len(scores_history_rows)} row(s).")
        print("\nDone.")

    if errored:
        # A per-symbol EODHD failure doesn't stop the rest of the run
        # (see the try/except above), but it's a real problem worth
        # surfacing loudly. Exiting non-zero here is what makes GitHub
        # Actions mark the run as failed (red X) instead of green --
        # otherwise a bad ticker would only ever show up as a line buried
        # in an otherwise-successful-looking log.
        print(f"\nFAILING: {len(errored)} symbol(s) errored: {[s for s, _ in errored]}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Actually write to Airtable (default: dry run).")
    args = parser.parse_args()

    symbols = TEST_SYMBOLS if TEST_MODE else [s["Symbol"] for s in airtable_client.get_active_symbols()]
    print(f"TEST_MODE={TEST_MODE} -- processing {len(symbols)} symbol(s): {symbols}")

    run(symbols, write=args.write)
