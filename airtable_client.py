"""
All Airtable reads and writes for the pipeline, talking directly to the
Airtable REST API (not the Airtable SDK, to keep dependencies minimal).

Field names used below were confirmed against the live base schema
(base appummriRwPGUNjsj) immediately before writing this file -- see
Config / Symbols / Indicators / Scores_Current field lists. If Airtable
columns are ever renamed, this file (and only this file) needs updating.

Write strategy per table:
  - Config, Symbols: read-only from this script's perspective.
  - Indicators, Scores_Current: one row per symbol, written each run via
    Airtable's server-side upsert (performUpsert) keyed on Symbol -- no
    separate "list existing rows" call needed, which keeps the per-run
    Airtable API-call count down (matters on the free-tier monthly cap).
    Scores_Current is upserted with typecast=True so a brand-new Group/
    Subgroup added in the Symbols table auto-creates the matching option
    instead of failing the run; Indicators (no single-selects) stays strict.

Dated history is intentionally not written here -- it used to be an
Airtable table (Scores_History) but is now appended to history_log.jsonl
in the repo instead (handled directly in main.py, not through this
module), to keep the Airtable base under its free-tier record limit.
See main.py's module docstring for the full reasoning.
"""

import time

import requests

from config import (
    AIRTABLE_TOKEN,
    AIRTABLE_BASE_ID,
    TABLE_CONFIG,
    TABLE_SYMBOLS,
    TABLE_INDICATORS,
    TABLE_SCORES_CURRENT,
)

AIRTABLE_API_URL = "https://api.airtable.com/v0/{base_id}/{table_id}"
_HEADERS = {"Authorization": f"Bearer {AIRTABLE_TOKEN}", "Content-Type": "application/json"}

# Reuse one connection to api.airtable.com across all calls instead of
# paying a fresh TCP/TLS handshake (~1s+) per request -- see the same
# fix in eodhd_client.py for the measurements that motivated this.
_session = requests.Session()

# Airtable's create/update endpoints accept at most 10 records per request.
BATCH_SIZE = 10

# Bounded retry+backoff for transient Airtable API failures. A single
# 30s read-timeout or a brief 429/5xx used to kill the whole daily run
# (observed 2026-08-02); these let it self-heal instead.
_MAX_ATTEMPTS = 4
_RETRY_5XX = {500, 502, 503, 504}


# --- Low-level helpers ---

def _request(method: str, url: str, *, idempotent: bool, **kwargs) -> requests.Response:
    """One Airtable request with bounded exponential-backoff retry on
    transient failures. Retries on connection errors and HTTP 429 for any
    method; also retries read-timeouts and 5xx when `idempotent` is True
    (safe for GET reads). Writes (idempotent=False) do NOT retry on a
    read-timeout or 5xx, since the write may actually have applied and a
    blind retry could duplicate a row. Non-retryable responses (e.g. 422)
    are returned as-is for the caller to handle."""
    kwargs.setdefault("timeout", 30)
    for attempt in range(_MAX_ATTEMPTS):
        last = attempt == _MAX_ATTEMPTS - 1
        try:
            response = _session.request(method, url, headers=_HEADERS, **kwargs)
        except requests.exceptions.ConnectionError:
            if last:
                raise
        except requests.exceptions.Timeout:
            if last or not idempotent:
                raise
        else:
            retryable = response.status_code == 429 or (idempotent and response.status_code in _RETRY_5XX)
            if not retryable or last:
                return response
        time.sleep(min(2 ** attempt, 8))  # 1s, 2s, 4s


def _list_all_records(table_id: str, params: dict = None) -> list:
    """Fetch every record matching `params`, following Airtable's cursor
    pagination until exhausted."""
    url = AIRTABLE_API_URL.format(base_id=AIRTABLE_BASE_ID, table_id=table_id)
    records = []
    query = dict(params or {})
    while True:
        response = _request("GET", url, idempotent=True, params=query)
        if response.status_code != 200:
            raise RuntimeError(
                f"Airtable list failed ({response.status_code}) for {table_id}: {response.text[:300]}"
            )
        data = response.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        query["offset"] = offset
    return records


def _batch_upsert(table_id: str, rows: list, merge_field: str, typecast: bool = False) -> None:
    """Create-or-update every row in one pass using Airtable's server-side
    upsert (performUpsert), matched on `merge_field` (e.g. "Symbol").

    This replaces the old "list every existing row to map Symbol->record
    id, then split into create vs update" approach -- Airtable now does the
    match inside the write itself, so each upsert costs one fewer full
    "list all records" API call per table per run. It also makes the write
    idempotent: a retried request re-matches the same row instead of
    duplicating it (so these calls are safe to mark idempotent for retry).

    Chunked to Airtable's 10-records/request limit. typecast defaults to
    False so a rejected write surfaces a real mismatch; Scores_Current
    passes typecast=True so a brand-new Group/Subgroup value auto-creates
    its single-select option instead of failing the run (the regimes in
    that same payload still come only from scoring.py's fixed vocabulary,
    so nothing unexpected gets created)."""
    url = AIRTABLE_API_URL.format(base_id=AIRTABLE_BASE_ID, table_id=table_id)
    for i in range(0, len(rows), BATCH_SIZE):
        chunk = rows[i:i + BATCH_SIZE]
        payload = {
            "performUpsert": {"fieldsToMergeOn": [merge_field]},
            "records": [{"fields": fields} for fields in chunk],
            "typecast": typecast,
        }
        response = _request("PATCH", url, idempotent=True, json=payload)
        if response.status_code != 200:
            raise RuntimeError(
                f"Airtable upsert failed ({response.status_code}) for {table_id}: {response.text[:300]}"
            )


# --- Config table (read-only) ---

def get_config() -> dict:
    """Reads the Config table into {Parameter: Value}. Blank placeholder
    rows (no Parameter set) are skipped."""
    records = _list_all_records(TABLE_CONFIG)
    config = {}
    for record in records:
        fields = record["fields"]
        parameter = fields.get("Parameter")
        value = fields.get("Value")
        if parameter and value is not None:
            config[parameter] = value
    return config


# --- Symbols table (read-only) ---

def get_active_symbols() -> list:
    """Reads Active=true rows from Symbols. Group/Subgroup come back as
    plain strings from the REST API (singleSelect fields serialize to
    their option name, not an object)."""
    records = _list_all_records(TABLE_SYMBOLS)
    symbols = []
    for record in records:
        fields = record["fields"]
        if not fields.get("Active"):
            continue
        symbols.append({
            "Symbol": fields.get("Symbol"),
            "Name": fields.get("Name"),
            "Group": fields.get("Group"),
            "Subgroup": fields.get("Subgroup"),
        })
    return symbols


# --- Indicators (latest row per symbol, replaced each run) ---

def upsert_indicators(rows: list) -> None:
    """rows: list of dicts with Airtable field names as keys (Symbol,
    Date, Close, MA60, MA120, ATR20, ATR90Avg, BBUpper, BBLower, BBMid,
    MACD, MACD_Signal, RSI14, ROC20). Replaces each symbol's existing row
    in place; Indicators holds only the latest pull per symbol, not a
    growing dated history."""
    _batch_upsert(TABLE_INDICATORS, rows, merge_field="Symbol")


# --- Scores_Current (latest row per symbol, replaced each run) ---

def upsert_scores_current(rows: list) -> None:
    """rows: list of dicts with Airtable field names as keys, one per
    symbol. Replaces each symbol's existing row in place.

    typecast=True so that a Group/Subgroup value newly created in the
    Symbols table (which this table inherits) auto-creates the matching
    single-select option here, instead of the whole run failing with a
    422 the first time a new category is added. This keeps "just add the
    security in Symbols" working with no schema step -- see DEPLOYMENT.md."""
    _batch_upsert(TABLE_SCORES_CURRENT, rows, merge_field="Symbol", typecast=True)
