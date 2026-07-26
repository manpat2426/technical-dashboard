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
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import airtable_client
import indicators
import scoring
from config import TEST_MODE, TEST_SYMBOLS, BACKFILL_MONTHS

# Written alongside repo root regardless of the working directory the
# script is invoked from -- read by the (future) GitHub Pages dashboard
# instead of it connecting to Airtable directly, so no Airtable token is
# ever exposed client-side.
DASHBOARD_JSON_PATH = Path(__file__).parent / "dashboard_data.json"

# Exactly the fields the dashboard needs from each Scores_Current row.
# Keeping this as an explicit list (rather than dumping the whole row)
# means internal/traceability-only columns (Price60, ATR_Ratio, BBW,
# etc.) never leak into the public JSON file.
DASHBOARD_JSON_FIELDS = [
    "Symbol", "Name", "Group", "Subgroup",
    "TechScore", "TechScore_1W", "TechScore_2W", "TechScore_3W",
    "TechScore_1M", "TechScore_2M", "TechScore_3M",
    "TrendRegime", "MomentumRegime", "VolatilityRegime",
    "TrendRegime_1W", "TrendRegime_2W", "TrendRegime_3W", "TrendRegime_1M",
    "MomentumRegime_1W", "MomentumRegime_2W", "MomentumRegime_3W", "MomentumRegime_1M",
    "VolatilityRegime_1W", "VolatilityRegime_2W", "VolatilityRegime_3W", "VolatilityRegime_1M",
]


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


# Checkpoint horizons for build_checkpoint_fields(). Week-based horizons
# get all 4 checkpoint columns (TechScore + all 3 regimes); month-based
# horizons only get TechScore, except 1M which also gets all 3 regimes
# -- matching the 6 TechScore / 12 regime column split requested.
_CHECKPOINT_WEEK_OFFSETS = [("1W", pd.Timedelta(days=7)),
                            ("2W", pd.Timedelta(days=14)),
                            ("3W", pd.Timedelta(days=21))]
_CHECKPOINT_MONTH_OFFSETS = [("1M", 1), ("2M", 2), ("3M", 3)]


def _nearest_snapshot_on_or_before(scored: pd.DataFrame, dates_dt: pd.Series, target) -> pd.Series | None:
    """The row in `scored` with the latest Date <= target, or None if
    every date in `scored` is after target (target predates this
    symbol's earliest scoreable history)."""
    candidates = scored[dates_dt <= target]
    if candidates.empty:
        return None
    return candidates.iloc[-1]


def build_checkpoint_fields(scored: pd.DataFrame, anchor_date: str) -> dict:
    """
    Historical checkpoint columns for Scores_Current: TechScore at
    1W/2W/3W/1M/2M/3M before `anchor_date`, plus TrendRegime/
    MomentumRegime/VolatilityRegime at 1W/2W/3W/1M before it. 1M/2M/3M
    use calendar-month subtraction (like Excel/Airtable's EDATE), so a
    31st can resolve to a shorter month's last day.

    Sourced from the same in-memory `scored` DataFrame that
    build_scores_history_rows() uses -- NOT a live Airtable query. That
    DataFrame already holds every scoreable date across the ~2-year pull
    (comfortably more than the 3-month max lookback here) with the exact
    same values Scores_History gets written from, so results are
    identical to what a live Scores_History read would return, without
    ~113 extra Airtable round-trips per run -- the same call made for
    the momentum lookback in scoring.py.

    Retrieval is "nearest available snapshot on or before the target
    date" (see _nearest_snapshot_on_or_before). If a target predates a
    symbol's earliest scoreable date, that checkpoint's keys are simply
    omitted from the returned dict -- Airtable leaves omitted fields
    blank/untouched on update, so this reads as "no data yet" rather
    than a wrong or erroring value.
    """
    anchor = pd.to_datetime(anchor_date)
    dates_dt = pd.to_datetime(scored["Date"])
    fields = {}

    for suffix, delta in _CHECKPOINT_WEEK_OFFSETS:
        row = _nearest_snapshot_on_or_before(scored, dates_dt, anchor - delta)
        if row is not None:
            fields[f"TechScore_{suffix}"] = row["TechScore"]
            fields[f"TrendRegime_{suffix}"] = row["TrendRegime"]
            fields[f"MomentumRegime_{suffix}"] = row["MomentumRegime"]
            fields[f"VolatilityRegime_{suffix}"] = row["VolatilityRegime"]

    for suffix, months in _CHECKPOINT_MONTH_OFFSETS:
        row = _nearest_snapshot_on_or_before(scored, dates_dt, anchor - pd.DateOffset(months=months))
        if row is not None:
            fields[f"TechScore_{suffix}"] = row["TechScore"]
            if suffix == "1M":
                fields["TrendRegime_1M"] = row["TrendRegime"]
                fields["MomentumRegime_1M"] = row["MomentumRegime"]
                fields["VolatilityRegime_1M"] = row["VolatilityRegime"]

    return fields


def build_scores_current_row(scored: pd.DataFrame, latest: pd.Series, name: str, group: str, subgroup: str) -> dict:
    """One symbol's latest computed scores plus historical checkpoint
    columns, for Scores_Current. Name/Group/Subgroup are all inherited
    directly from the Symbols table. Group/Subgroup are single-select
    fields here (inherited from the Symbols table's own Group/Subgroup
    select fields) -- unlike the old plain-text Group field, an empty/
    invalid value would be rejected by Airtable (typecast is off), so
    callers must pass None rather than "" when a symbol has no Group/
    Subgroup, letting _to_native leave the field blank instead of
    erroring."""
    fields = {
        "Symbol": latest["Symbol"],
        "Name": name,
        "Group": group,
        "Subgroup": subgroup,
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
    }
    fields.update(build_checkpoint_fields(scored, latest["Date"]))
    return _to_native(fields)


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


def build_dashboard_json(scores_current_rows: list) -> dict:
    """
    The public dashboard payload: {"last_updated": <UTC ISO timestamp>,
    "symbols": [one record per successfully-processed active symbol]}.

    Each record has exactly DASHBOARD_JSON_FIELDS, always in the same
    shape -- a checkpoint that was left blank in Scores_Current (e.g. a
    symbol too new to have 3M of history) comes through as JSON `null`
    via dict.get() rather than the key being missing, so the dashboard
    can rely on every record having the same fields.

    scores_current_rows already has Airtable-safe native types (from
    _to_native() in build_scores_current_row), so this is just a
    reshape -- no further conversion needed for JSON serialization.
    """
    return {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "symbols": [
            {field: row.get(field) for field in DASHBOARD_JSON_FIELDS}
            for row in scores_current_rows
        ],
    }


def write_dashboard_json(data: dict, path: Path) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


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
        # Scores_History.Group is still a plain-text field, so "" is a
        # safe default there. Scores_Current.Group/Subgroup are
        # single-select fields now -- pass None (not "") when missing,
        # per build_scores_current_row's docstring.
        group = symbol_info.get("Group") or ""
        group_select = symbol_info.get("Group")
        subgroup_select = symbol_info.get("Subgroup")
        name = symbol_info.get("Name")

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
        scores_current_rows.append(build_scores_current_row(scored, latest, name, group_select, subgroup_select))

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

        # Written only on a real --write run, not a dry run: this file
        # should always reflect the same "final" state that was just
        # pushed to Airtable, never data that was only computed locally.
        dashboard_data = build_dashboard_json(scores_current_rows)
        write_dashboard_json(dashboard_data, DASHBOARD_JSON_PATH)
        print(f"  {DASHBOARD_JSON_PATH.name}: wrote {len(dashboard_data['symbols'])} symbol(s), "
              f"last_updated={dashboard_data['last_updated']}.")
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
