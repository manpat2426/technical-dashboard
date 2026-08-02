"""
All Airtable reads and writes for the pipeline, talking directly to the
Airtable REST API (not the Airtable SDK, to keep dependencies minimal).

Field names used below were confirmed against the live base schema
(base appummriRwPGUNjsj) immediately before writing this file -- see
Config / Symbols / Indicators / Scores_Current field lists. If Airtable
columns are ever renamed, this file (and only this file) needs updating.

Write strategy per table:
  - Config, Symbols: read-only from this script's perspective.
  - Indicators, Scores_Current: one row per symbol, replaced in place
    each run (upsert keyed by Symbol). Scores_Current is written with
    typecast=True so a brand-new Group/Subgroup added in the Symbols
    table auto-creates the matching option instead of failing the run;
    Indicators (no single-selects) stays strict.

Dated history is intentionally not written here -- it used to be an
Airtable table (Scores_History) but is now appended to history_log.jsonl
in the repo instead (handled directly in main.py, not through this
module), to keep the Airtable base under its free-tier record limit.
See main.py's module docstring for the full reasoning.
"""

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


# --- Low-level helpers ---

def _list_all_records(table_id: str, params: dict = None) -> list:
    """Fetch every record matching `params`, following Airtable's cursor
    pagination until exhausted."""
    url = AIRTABLE_API_URL.format(base_id=AIRTABLE_BASE_ID, table_id=table_id)
    records = []
    query = dict(params or {})
    while True:
        response = _session.get(url, headers=_HEADERS, params=query, timeout=30)
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


def _batch_create(table_id: str, field_rows: list, typecast: bool = False) -> None:
    """Create records, chunked to Airtable's 10-per-request limit.

    typecast defaults to False, so a rejected write means a real mismatch
    worth seeing rather than something silently papered over. Scores_Current
    passes typecast=True (see upsert_scores_current) so that a brand-new
    Group/Subgroup created in the Symbols table auto-creates the matching
    single-select option here instead of failing the whole run -- the
    regimes written in that same payload still come only from scoring.py's
    fixed vocabulary, so nothing unexpected gets created."""
    url = AIRTABLE_API_URL.format(base_id=AIRTABLE_BASE_ID, table_id=table_id)
    for i in range(0, len(field_rows), BATCH_SIZE):
        chunk = field_rows[i:i + BATCH_SIZE]
        payload = {"records": [{"fields": fields} for fields in chunk], "typecast": typecast}
        response = _session.post(url, headers=_HEADERS, json=payload, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(
                f"Airtable create failed ({response.status_code}) for {table_id}: {response.text[:300]}"
            )


def _batch_update(table_id: str, id_field_pairs: list, typecast: bool = False) -> None:
    """Update existing records by record id, chunked to 10 per request.
    See _batch_create for the meaning of typecast."""
    url = AIRTABLE_API_URL.format(base_id=AIRTABLE_BASE_ID, table_id=table_id)
    for i in range(0, len(id_field_pairs), BATCH_SIZE):
        chunk = id_field_pairs[i:i + BATCH_SIZE]
        payload = {
            "records": [{"id": record_id, "fields": fields} for record_id, fields in chunk],
            "typecast": typecast,
        }
        response = _session.patch(url, headers=_HEADERS, json=payload, timeout=30)
        if response.status_code != 200:
            raise RuntimeError(
                f"Airtable update failed ({response.status_code}) for {table_id}: {response.text[:300]}"
            )


def _upsert_by_key(table_id: str, rows: list, existing_by_key: dict, key_fn, typecast: bool = False) -> None:
    """Shared upsert logic: split `rows` into creates vs. updates based on
    whether key_fn(row) is already present in existing_by_key (key ->
    record id), then write both batches. `typecast` is passed through to
    both (see _batch_create)."""
    to_create, to_update = [], []
    for row in rows:
        record_id = existing_by_key.get(key_fn(row))
        if record_id:
            to_update.append((record_id, row))
        else:
            to_create.append(row)

    if to_create:
        _batch_create(table_id, to_create, typecast=typecast)
    if to_update:
        _batch_update(table_id, to_update, typecast=typecast)


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
    existing = _list_all_records(TABLE_INDICATORS, {"fields[]": ["Symbol"]})
    existing_by_symbol = {
        r["fields"]["Symbol"]: r["id"] for r in existing if r["fields"].get("Symbol")
    }
    _upsert_by_key(TABLE_INDICATORS, rows, existing_by_symbol, key_fn=lambda row: row["Symbol"])


# --- Scores_Current (latest row per symbol, replaced each run) ---

def upsert_scores_current(rows: list) -> None:
    """rows: list of dicts with Airtable field names as keys, one per
    symbol. Replaces each symbol's existing row in place.

    typecast=True so that a Group/Subgroup value newly created in the
    Symbols table (which this table inherits) auto-creates the matching
    single-select option here, instead of the whole run failing with a
    422 the first time a new category is added. This keeps "just add the
    security in Symbols" working with no schema step -- see DEPLOYMENT.md."""
    existing = _list_all_records(TABLE_SCORES_CURRENT, {"fields[]": ["Symbol"]})
    existing_by_symbol = {
        r["fields"]["Symbol"]: r["id"] for r in existing if r["fields"].get("Symbol")
    }
    _upsert_by_key(TABLE_SCORES_CURRENT, rows, existing_by_symbol,
                   key_fn=lambda row: row["Symbol"], typecast=True)
