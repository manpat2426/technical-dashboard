"""
Orchestrates the full pipeline: for each symbol, pull + merge raw
indicators (indicators.py), score every date (scoring.py), classify every
date into a decision state (decision.py), then write the latest values to
Indicators and Scores_Current (airtable_client.py).

Historical snapshots are NOT written to Airtable -- nothing in this
pipeline ever reads dated history back (the 1W-3M checkpoints and the
momentum ~5-day lookback are recomputed in-memory from the same EODHD
pull every run; see build_checkpoint_fields() below and
scoring.compute_derived_series()). Instead, a plain audit log of every
day's computed scores/regimes is appended to history_log.jsonl in the
repo (one JSON object per line): the very first run bootstraps it with
every scoreable date from the ~2-year EODHD pull (free to do -- that
data is already computed in memory), and every run after that just
appends each symbol's latest date. This used to be an Airtable table
(Scores_History) but was moved to a repo file to keep the Airtable base
under its free-tier record limit; see DEPLOYMENT.md.

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
import decision
import indicators
import scoring
from config import TEST_MODE, TEST_SYMBOLS

# Written alongside repo root regardless of the working directory the
# script is invoked from -- read by the (future) GitHub Pages dashboard
# instead of it connecting to Airtable directly, so no Airtable token is
# ever exposed client-side.
DASHBOARD_JSON_PATH = Path(__file__).parent / "dashboard_data.json"

# Plain append-only history log (repo root). Its presence/absence is how
# run() decides whether this is the first-ever run (full backfill) or a
# normal day (append latest only) -- see build_history_log_rows().
HISTORY_LOG_PATH = Path(__file__).parent / "history_log.jsonl"

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
    # Decision layer (decision.py). Carried in the JSON feed so the
    # dashboard can display it -- index.html does not render these yet.
    "Decision", "Decision_1W", "Decision_2W", "Decision_3W", "Decision_1M",
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
    MomentumRegime/VolatilityRegime/Decision at 1W/2W/3W/1M before it.
    1M/2M/3M use calendar-month subtraction (like Excel/Airtable's
    EDATE), so a 31st can resolve to a shorter month's last day.

    Decision comes straight off the `scored` frame's point-in-time
    Decision column (added by decision.add_decision_series), so
    Decision_1W is genuinely what the tool would have said a week ago --
    not today's decision back-dated.

    Sourced entirely from the in-memory `scored` DataFrame computed
    earlier in run() -- NOT an Airtable query. That DataFrame already
    holds every scoreable date across the ~2-year EODHD pull (comfortably
    more than the 3-month max lookback here), so this is a pure in-memory
    lookback with no Airtable round-trips involved -- the same approach
    used for the momentum lookback in scoring.py.

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
            fields[f"Decision_{suffix}"] = row["Decision"]

    for suffix, months in _CHECKPOINT_MONTH_OFFSETS:
        row = _nearest_snapshot_on_or_before(scored, dates_dt, anchor - pd.DateOffset(months=months))
        if row is not None:
            fields[f"TechScore_{suffix}"] = row["TechScore"]
            if suffix == "1M":
                fields["TrendRegime_1M"] = row["TrendRegime"]
                fields["MomentumRegime_1M"] = row["MomentumRegime"]
                fields["VolatilityRegime_1M"] = row["VolatilityRegime"]
                fields["Decision_1M"] = row["Decision"]

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
        "Decision": latest["Decision"],
    }
    fields.update(build_checkpoint_fields(scored, latest["Date"]))
    return _to_native(fields)


# The five Scores_Current fields whose values must come from
# decision.DECISION_STATES and nothing else.
_DECISION_FIELDS = ["Decision", "Decision_1W", "Decision_2W", "Decision_3W", "Decision_1M"]


def validate_decision_fields(rows: list) -> None:
    """
    Fails the run if any Decision* value in `rows` isn't one of the six
    states. Call this on the assembled Scores_Current rows before they're
    written.

    Scores_Current is upserted with typecast=True, so that a brand-new
    Group/Subgroup added in Symbols auto-creates its single-select option
    instead of failing the run (see airtable_client.upsert_scores_current).
    That convenience cuts exactly the wrong way for these five fields: a
    malformed decision string wouldn't be rejected, it would be silently
    accepted and created as a SEVENTH option, quietly corrupting a
    vocabulary that is supposed to be closed at six -- and it would keep
    doing so every run afterwards, with the bad option looking just as
    legitimate as the real ones in the Airtable UI.

    So the strictness typecast removes gets restored here, for these
    fields specifically, rather than by turning typecast off base-wide
    (which would reintroduce the new-Group/Subgroup run failure it was
    added to fix). Raising rather than asserting is deliberate: `assert`
    compiles away under `python -O`, and this guard has to hold in every
    environment.

    None is allowed and expected: a checkpoint with no snapshot that far
    back, or a date too early to have a full smoothing window behind it,
    legitimately writes blank.
    """
    allowed = set(decision.DECISION_STATES)
    offenders = [
        (row.get("Symbol"), field, row[field])
        for row in rows
        for field in _DECISION_FIELDS
        if row.get(field) is not None and row[field] not in allowed
    ]
    if offenders:
        raise RuntimeError(
            f"Refusing to write Scores_Current: {len(offenders)} Decision value(s) outside "
            f"the closed six-state set {sorted(allowed)}. Because Scores_Current writes with "
            f"typecast=True, writing these would silently create new single-select options "
            f"rather than fail. Offending (Symbol, field, value): {offenders[:10]}"
        )


def build_history_log_rows(rows: pd.DataFrame) -> list:
    """One dict per row of `rows` (a scored DataFrame), in the shape
    appended to history_log.jsonl: Symbol, Date, every score/regime
    column, and the decision-layer state. Same fields the old Airtable
    Scores_History table held, minus Group/SnapshotType/Notes (this is a
    plain append-only log, not a table needing per-run bookkeeping
    fields), plus Decision.

    Decision is the point-in-time value for that row's own date (see
    decision.add_decision_series) -- on a first-run backfill each logged
    date carries the decision as it stood on that date, not today's.
    Dates too early to have a full smoothing window come through as
    null.

    Pass the full `scored` DataFrame for a first-run backfill (every
    scoreable date), or just its last row (e.g. `scored.tail(1)`) for a
    normal day's single append -- both go through the same shape here."""
    return [
        _to_native({
            "Symbol": record["Symbol"],
            "Date": record["Date"],
            "TrendScore": record["TrendScore"],
            "MomentumAdj": record["MomentumAdj"],
            "VolAdj": record["VolAdj"],
            "TechScore": record["TechScore"],
            "TrendRegime": record["TrendRegime"],
            "MomentumRegime": record["MomentumRegime"],
            "VolatilityRegime": record["VolatilityRegime"],
            "Decision": record["Decision"],
        })
        for record in rows.to_dict("records")
    ]


def append_history_log(rows: list, path: Path) -> None:
    """Appends one JSON object per line to history_log.jsonl. Deliberately
    no dedupe against existing (Symbol, Date) lines -- an occasional
    duplicate row from a same-day re-run is harmless for an audit log and
    not worth the complexity of checking."""
    with open(path, "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


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


def run(write: bool, only_symbols: list = None):
    config = airtable_client.get_config()
    print(f"Loaded {len(config)} config parameters from Airtable.")

    # Fetched exactly once per run (this used to be read a second time in
    # __main__ just to build the ticker list -- an extra Airtable list
    # call every run for no reason). `only_symbols` is the TEST_MODE
    # subset; when None we process the whole active universe.
    active_symbols = {s["Symbol"]: s for s in airtable_client.get_active_symbols()}
    symbols_to_process = only_symbols if only_symbols is not None else list(active_symbols.keys())
    print(f"TEST_MODE={TEST_MODE} -- processing {len(symbols_to_process)} symbol(s): {symbols_to_process}")

    # Determined once, up front: history_log.jsonl doesn't exist yet only
    # on the very first run ever (it's created at the end of this
    # function, and future runs check out whatever was committed last
    # time), so this correctly distinguishes "bootstrap with full
    # history" from "just append today" for the whole run.
    first_history_run = not HISTORY_LOG_PATH.exists()

    indicators_rows = []
    scores_current_rows = []
    history_log_rows = []
    skipped = []
    errored = []

    for symbol in symbols_to_process:
        symbol_info = active_symbols.get(symbol)
        if symbol_info is None:
            print(f"\n{symbol}: not found (or not Active) in the Symbols table -- skipping.")
            skipped.append(symbol)
            continue
        # Scores_Current.Group/Subgroup are single-select fields --
        # pass None (not "") when missing, per build_scores_current_row's
        # docstring.
        group_select = symbol_info.get("Group")
        subgroup_select = symbol_info.get("Subgroup")
        name = symbol_info.get("Name")

        print(f"\n{symbol}: pulling from EODHD and scoring...")
        try:
            history = indicators.build_symbol_history(symbol, config)
            scored = scoring.score_series(history, config)
            # The decision layer sits on top of the finished scores: one
            # forward pass over this symbol's full scored history, adding
            # the point-in-time Decision for every date. Everything
            # downstream (Scores_Current, its Decision checkpoints, the
            # history log, the dashboard JSON) reads that column.
            scored = decision.add_decision_series(scored)
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

        history_log_rows.extend(build_history_log_rows(scored if first_history_run else scored.tail(1)))

        print(f"  {len(scored)} scoreable dates total "
              f"(latest: {latest['Date']}, TechScore={latest['TechScore']}, "
              f"sTech={latest['sTech']:.2f}, Decision={latest['Decision']}).")

    # Closed-set guard, run before ANY write (and on dry runs too, so a
    # bad value surfaces without needing --write). A decision string
    # outside the six-state vocabulary means a bug in decision.py, not a
    # data problem -- fail the whole run loudly rather than let
    # typecast=True quietly mint a seventh option in Airtable.
    validate_decision_fields(scores_current_rows)

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
    print(f"{HISTORY_LOG_PATH.name} : {len(history_log_rows)} row(s) total "
          f"({'initial backfill' if first_history_run else 'latest-day append'})")

    if not write:
        print("\nDry run only -- nothing written to Airtable. Re-run with --write to apply.")
    else:
        print("\nWriting to Airtable...")
        airtable_client.upsert_indicators(indicators_rows)
        print(f"  Indicators: wrote {len(indicators_rows)} row(s).")
        airtable_client.upsert_scores_current(scores_current_rows)
        print(f"  Scores_Current: wrote {len(scores_current_rows)} row(s).")

        if history_log_rows:
            append_history_log(history_log_rows, HISTORY_LOG_PATH)
            print(f"  {HISTORY_LOG_PATH.name}: appended {len(history_log_rows)} row(s)"
                  f"{' (initial backfill)' if first_history_run else ''}.")

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

    # In TEST_MODE, restrict to TEST_SYMBOLS; otherwise run() processes the
    # whole active universe from its single get_active_symbols() fetch.
    run(write=args.write, only_symbols=TEST_SYMBOLS if TEST_MODE else None)
