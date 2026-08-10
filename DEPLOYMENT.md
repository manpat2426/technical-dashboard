# Technical Analysis Dashboard — Deployment Guide

*This document explains the **how** of the system: what services it runs on, how they're wired together, what the code actually does, and how to operate and troubleshoot it. It is the companion to `METHODOLOGY.md`, which covers the **what** and **why** of the scoring model itself.*

*Everything below was verified directly against the live repository, the live Airtable base (`appummriRwPGUNjsj`), the live GitHub repo (`manpat2426/technical-dashboard`) and its Actions run history — not written from memory or assumption. Anywhere that couldn't be verified this way is called out explicitly.*

---

## 1. Architecture Overview

Four components, one direction of data flow:

```
EODHD (market data API)
      │  EOD prices + technical indicators, pulled over HTTPS
      ▼
GitHub Actions runner (Python pipeline: main.py + helper modules)
      │  merges raw data, computes derived values, scores, and regimes
      │
      ├──▶ writes to Airtable
      │     (Config & Symbols are read-only inputs; Indicators and
      │      Scores_Current are written — current-state only, no
      │      dated history)
      │
      └──▶ writes dashboard_data.json AND appends to history_log.jsonl
            in the repo, then the SAME workflow run commits both files
            back to `main`
                  │
                  ▼
      GitHub Pages serves the repo's static files (index.html + dashboard_data.json)
                  │
                  ▼
      Browser loads index.html, which fetches dashboard_data.json client-side
```

**The core design principle: methodology lives in the script, Airtable stores and displays the current state, the dashboard reads a static JSON snapshot, and a plain repo file (`history_log.jsonl`) keeps the permanent day-by-day history — never Airtable directly.**

- All scoring logic, thresholds-that-are-actually-tunable, and regime rules live in the Python files in this repo (`scoring.py` above all). Airtable is not a spreadsheet-with-formulas; it's a **record store and viewer**. This keeps the methodology in one traceable, version-controlled place instead of split between code and hidden Airtable formulas.
- Airtable holds every current input and derived value for the latest date per symbol (see Section 3), so a human can inspect *why* today's score came out the way it did without re-running anything. Dated history is **not** kept in Airtable — it's appended to `history_log.jsonl` in the repo instead (see Section 3 and Section 10), which is what keeps the Airtable base's own record count small.
- The dashboard (`index.html`) never talks to Airtable and carries no Airtable token. It only ever fetches `dashboard_data.json`, a plain static file sitting next to it in the same GitHub Pages deployment. This is what makes it safe to make the repo (and therefore the dashboard) public — there is no credential-bearing code running in the browser.

---

## 2. Accounts, Services & Costs

| Service | Role | Verified details | Cost |
|---|---|---|---|
| **EODHD** | Market data API (EOD prices + technical indicators) | API key stored as a GitHub Actions secret (`EODHD_API_KEY`). Code comments in [config.py](config.py:43) document an assumed budget of **100,000 credits/day** and an assumed **~1,000 requests/minute** cap, with each *technical* endpoint call costing 5 credits (EOD price calls are not stated to cost extra). A full run pulls 1 EOD + 6 technical series per symbol × 113 symbols ≈ 800 requests/day, of which 6×113=678 are "technical" (5 credits each) ≈ **3,390 technical credits/day** — comfortably inside the assumed 100,000/day budget. | ⚠️ **Unverified** — the actual EODHD subscription tier and its monthly price aren't recorded anywhere in the repo or accessible to me. Confirm directly on your EODHD account dashboard. |
| **GitHub** | Hosts the repo, runs the daily pipeline (Actions), serves the dashboard (Pages) | Repo `manpat2426/technical-dashboard` is confirmed **public**. Actions minutes and Pages hosting are free for public repositories. | **$0/month** (verified — public repo) |
| **Airtable** | Stores Config/Symbols/Indicators/Scores_Current | Base `appummriRwPGUNjsj`, **4 tables**, ~363 records total (verified live). The `Scores_History` table — which had grown to 15,142 records and was the sole reason this base exceeded the free tier — was deleted on 2026-07-27; history now lives in `history_log.jsonl` in the repo instead (see Section 3, Section 10). | ⚠️ **Action needed on your end** — the base's record count is now well under the free tier's 1,000/base limit, but I can't see or change your Airtable billing plan through the API. If you upgraded specifically because of Scores_History, confirm on your Airtable account/billing page that you can downgrade back to free now. |

**Monthly cost tally:** GitHub $0 (confirmed). EODHD costs still need to be filled in from your own account billing page. Airtable should now be $0/month too, once you confirm the plan downgrade above — the record-count blocker that required a paid plan is gone.

---

## 3. The Airtable Schema (verified live 2026-07-27; Scores_Current re-verified and extended 2026-08-02)

Base: `appummriRwPGUNjsj`. **Four tables** (`Scores_History` was deleted on 2026-07-27 — see below and Section 10), exactly as `airtable_client.py` documents.

### Config
*Control panel for every adjustable model parameter — periods, thresholds, tolerances.*

| Field | Type | Notes |
|---|---|---|
| Parameter | Single line text | Primary field, e.g. `MA_Trend_Short`, `PRICE60_HIGH` |
| Value | Number | Decimals allowed (e.g. 1.05, 0.80) |
| Category | Single select | Trend / Momentum / Volatility |
| Notes | Long text | Free-form explanation |

Currently **24 rows total, 21 with real parameter values** (3 rows are blank placeholders left over from initial setup — `get_config()` in `airtable_client.py` explicitly skips any row with no `Parameter` set, so these are harmless but worth deleting for tidiness).

### Symbols
*The universe of tracked securities. Grouping lives only here — every other table inherits Group/Subgroup from Symbols.*

| Field | Type | Notes |
|---|---|---|
| Symbol | Single line text | Primary field. EODHD ticker format, e.g. `AAPL.US` |
| Name | Single line text | Security name |
| Group | Single select | Primary grouping (Stocks / ETFs) |
| Subgroup | Single select | Finer grouping (sector, asset class, etc.) |
| Active | Checkbox | Whether the symbol is included in the daily run |
| Notes | Long text | Optional |

Currently **113 active symbols**: **75 Stocks**, **38 ETFs**, verified live.

### Indicators
*Raw technical values pulled from EODHD, one row per symbol — latest pull only, replaced in place each run. No scores or regimes here.*

| Field | Type | Notes |
|---|---|---|
| Symbol | Single line text | Primary field |
| Date | Date | Observation date |
| Close | Number | Closing price (split+dividend adjusted) |
| MA60 | Number | 60-day moving average |
| MA120 | Number | 120-day moving average |
| ATR20 | Number | 20-period ATR |
| ATR90Avg | Number | 90-day average of the ATR series, computed by the script |
| BBUpper | Number | Upper Bollinger Band |
| BBLower | Number | Lower Bollinger Band |
| BBMid | Number | Bollinger Band midpoint |
| MACD | Number | MACD line (EMA12 − EMA26) |
| MACD_Signal | Number | EMA9 of MACD |
| RSI14 | Number | 14-period RSI |
| ROC20 | Number | 20-period rate of change, computed by the script |

### Scores_Current
*The active calculation layer: one row per symbol for the most recent valid date. Every column is written by the script, including the historical checkpoint columns.*

| Field | Type | Notes |
|---|---|---|
| Symbol | Single line text | Primary field |
| Name, Group, Subgroup | Text / Single select / Single select | Inherited from Symbols. Group/Subgroup are single-select here (not plain text) so Airtable Interfaces can filter on them |
| Date | Date | Calculation date |
| Price60, Price120 | Number | Close/MA60, Close/MA120 |
| MAAlign | Number | 1 or 0 |
| Trend60Score, Trend120Score, MAAlignScore, TrendScore | Number | See METHODOLOGY.md §5 |
| TrendRegime | Single select | Plain-language trend state |
| MACD, MACD_Signal, RSI14, ROC20 | Number | Carried in for traceability |
| MomentumRegime | Single select | Plain-language momentum state |
| MomentumAdj | Number | −3 to +3 |
| ATR_Ratio, BBW, BBW_Percentile | Number | Derived volatility inputs |
| VolatilityRegime | Single select | Plain-language volatility state |
| VolAdj | Number | −2 to +2 |
| TechScore | Number | Final combined score |
| Decision | Single select | Decision-layer state — one of six (see below) |
| TechScore_1W / 2W / 3W / 1M / 2M / 3M | Number | Historical checkpoints |
| TrendRegime_1W / 2W / 3W / 1M | Single select | |
| MomentumRegime_1W / 2W / 3W / 1M | Single select | |
| VolatilityRegime_1W / 2W / 3W / 1M | Single select | |
| Decision_1W / 2W / 3W / 1M | Single select | Decision at each checkpoint |

**The five `Decision*` fields were added to the live base on 2026-08-02** (verified created via the Airtable metadata API, then verified end-to-end by an actual `Scores_Current` upsert that Airtable accepted and that read back correctly). All five are **single-select**, and each one had **all six state strings pre-created as options**:

`Improving` · `Steady-constructive` · `Neutral / No Signal` · `Concerning` · `Steady-weak` · `Exit`

Pre-creating every option on every field matters: the option list is per-field in Airtable, so `Decision_3W` having "Exit" does not help `Decision_1M`. The exact strings are defined once in `decision.py`'s `DECISION_STATES` — rename one there and the matching Airtable option has to be renamed too, or that symbol's write will be rejected.

### history_log.jsonl (repo file, not Airtable)
*Dated snapshots of the full technical state, one line per symbol per date. This used to be the `Scores_History` Airtable table (schema above, for reference) but was moved to a plain repo file on 2026-07-27 to keep the Airtable base under its free-tier record limit — see Section 10 for the full reasoning.*

- **Location**: `history_log.jsonl` in the repo root, committed by the same GitHub Actions step that commits `dashboard_data.json` (see Section 6).
- **Format**: one JSON object per line (JSONL, not a JSON array) — `{"Symbol": ..., "Date": ..., "TrendScore": ..., "MomentumAdj": ..., "VolAdj": ..., "TechScore": ..., "TrendRegime": ..., "MomentumRegime": ..., "VolatilityRegime": ..., "Decision": ...}`. Same score/regime fields the old Scores_History table held, minus `Group`/`SnapshotType`/`Notes` (this is a plain log, not a table needing per-run bookkeeping columns), **plus `Decision`, added 2026-08-02**.
- **`Decision` is the point-in-time value for that line's own date** — what the tool would have said on that date using only data up to it, not today's decision back-dated (see Section 5's `decision.py` entry). It is `null` for any date too early in a symbol's history to have a full 15-day smoothing window behind it.
- **Lines written before 2026-08-02 have no `Decision` key at all** — the log is append-only and is never rewritten, so anything reading the whole file has to tolerate the key being absent on older lines (not just null). Only the field set changed; no existing line was touched.
- **Bootstrap-then-append**: the very first run ever (detected by the file not existing yet) writes every scoreable date across the full ~2-year EODHD pull, for every processed symbol — free to do, since that data is already computed in memory for the checkpoint calculations. Every run after that appends just the single latest date per symbol.
- **No dedupe, by design**: unlike the old Airtable table (upserted by Symbol+Date), this is a plain append-only log — a same-day re-run appends a duplicate-looking (Symbol, same Date) line, and that's considered harmless for an audit log rather than worth the complexity of checking.
- **Known limitation, also by design**: a symbol added to Symbols *after* the initial bootstrap won't retroactively get historical rows here — it only starts appearing from the day it's added onward. Acceptable because nothing in the live pipeline ever reads this file back (see Section 5) — it exists purely for ad-hoc historical analysis.

---

## 4. Credentials & Secrets

Two secrets, and they never live in code:

- **`EODHD_API_KEY`** and **`AIRTABLE_TOKEN`** are stored as **GitHub Actions repository secrets** (confirmed present via `gh secret list`). The workflow injects them as environment variables only for the duration of the `python main.py --write` step ([daily-run.yml](.github/workflows/daily-run.yml:38)).
- **Locally**, the same two variables are read from a `.env` file via `python-dotenv` ([config.py](config.py:14)). `.env` is listed in [.gitignore](.gitignore:2) and is never committed. `.env.example` (committed, no real values) documents the two variables to copy in.
- `config.py` fails fast with a clear `RuntimeError` if either variable is missing, rather than silently running with no credentials.
- No other file references these values directly — every other module imports them from `config.py`.

---

## 5. The Script

The pipeline is five small modules plus an orchestrator, verified by reading each file directly.

**`config.py`** — loads `.env`, fails loudly if secrets are missing, and defines structural constants: the Airtable base/table IDs, the two EODHD URL templates, the rate-limit pause (0.4s between EODHD calls), `TEST_MODE`/`TEST_SYMBOLS` (currently `TEST_MODE = False`, so a full run processes all active Symbols), and `HISTORY_YEARS = 2` (how much EODHD history to pull per symbol — comfortably exceeds the 3-month max checkpoint lookback plus the longest indicator lookback, e.g. BBW_Percentile's 125-period window). Model *parameters* (MA periods, thresholds) are deliberately **not** here — see Section 3's Config table.

**`eodhd_client.py`** — thin HTTP wrapper. `get_eod(symbol)` pulls ~2 years of daily prices, using EODHD's `adjusted_close` field (**split- and dividend-adjusted**, not raw close). `get_technical(symbol, function, period)` pulls one technical series at a time (`sma`, `atr`, `rsi`, `bbands`, `macd`), also computed by EODHD on adjusted data. Reuses one `requests.Session()` across all calls — the file's own comment notes this cuts the dominant cost of a full run (TCP/TLS handshake overhead) from ~1.1–1.3s to ~0.1–0.25s per request.

**`indicators.py`** — `build_symbol_history(symbol, config)` calls the EODHD client for EOD prices plus SMA(60), SMA(120), ATR, RSI, Bollinger Bands, and MACD (periods read from the live `config` dict, not hardcoded), then outer-merges them all into one date-sorted DataFrame. This is the one place that translates EODHD's field names (`uband`, `signal`, etc.) into the pipeline's internal column names.

**`scoring.py`** — the model itself:
1. `compute_derived_series()` adds `ATR90Avg` (rolling mean of ATR20), `ROC20` (Close vs. Close N periods ago), `BBW` (normalized Bollinger Band width), `BBW_Percentile` (vectorized rolling percentile rank), `ATR_Ratio` (ATR20/ATR90Avg), and the "5 trading days ago" prior-value columns used by the Improving/Deteriorating regimes.
2. `score_row()` computes Price60/Price120, MAAlign, the Trend/Momentum/Volatility scores and regimes, and the final `TechScore` for one date, reading thresholds from `config`.
3. `score_series()` runs `score_row()` across every date that has a complete lookback (drops early dates lacking enough history for MA120/ATR90Avg/BBW's 125-day window).

**`decision.py`** — the decision layer (added 2026-08-02). Sits strictly **on top of** the finished scores: it reads `TechScore` and nothing else, and changes no part of the scoring model — `scoring.py` was not modified when this was added.

- **The constants block.** All eight tunable numbers live in one labeled, commented block at the very top of the file (`SMOOTHING_WINDOW`, `HIGH_BAND_CUTOFF`, `LOW_BAND_CUTOFF`, `BAND_HYSTERESIS_BUFFER`, `HIGH_BAND_FADE_DROP`, `MID_BAND_SLOPE`, `LOW_BAND_EXIT_DROP`, `DECISION_PERSIST_DAYS`), deliberately **not** scattered inline the way the momentum-regime constants are in `scoring.py`. They are construction-based absolute values, not distribution-fitted. Like the momentum constants — and unlike the Config-table thresholds — tuning them means editing this file, committing, and pushing.
- **`sTech`** is a trailing rolling mean of `TechScore` over `SMOOTHING_WINDOW` (15) trading days. Every level and slope reading uses `sTech`, never raw `TechScore`. It exists because `TechScore` is built from hard threshold cutoffs and so can jump several points on a move that means nothing.
- **`add_decision_series(scored)`** is the whole public surface: it adds an `sTech` column and a `Decision` column to one symbol's scored history. It runs as a **stateful forward pass, oldest date → newest**, carrying the current band, the current official decision, and the pending-state counter — because smoothing, band hysteresis, and transition persistence all depend on the days before a given date. Each date's `Decision` is therefore the honest point-in-time value; today's decision is never applied backward over history.
- **Two dimensions:** the *level* of `sTech` (High / Middle / Low band, with `BAND_HYSTERESIS_BUFFER` required to exit a band you're already in) and its *slope*. Slope spacing is level-dependent — the High band reads month-scale (now vs ~1M, confirmed against ~2M), Middle and Low read week-over-week (now vs 1W vs 2W vs 3W). All steps must agree in direction or the slope reads flat, and a lookback with no data available also reads flat, so an unconfirmable slope can never move the decision.
- **Six states**, first-match-wins within each band, same top-down convention as the regimes in `scoring.py`: `Improving`, `Steady-constructive`, `Neutral / No Signal`, `Concerning`, `Steady-weak`, `Exit`.
- **`DECISION_PERSIST_DAYS`** (2): a newly-computed state only becomes the official decision after holding that many consecutive days; until then the prior official decision carries forward. Bootstrap exception — the first date that produces a state adopts it immediately, since there is no prior decision to carry.
- **Lookback retrieval** reuses the same nearest-prior-date rule as the checkpoint columns (latest snapshot on or before the target date), expressed as a vectorized `searchsorted` over the date column rather than a per-row DataFrame slice, since the forward pass needs five of these per date across ~2 years × 113 symbols.

**`airtable_client.py`** — all Airtable I/O, via the raw REST API (no SDK dependency). Config and Symbols are read-only. Indicators and Scores_Current are **replaced in place, one row per symbol** on every run — Airtable now holds only current-state data, no dated history (see Section 3's `history_log.jsonl` entry for where that went). Indicators writes use `typecast=False`, on purpose: a rejected write means a genuine mismatch (e.g. a regime string that doesn't match an existing single-select option) worth seeing, not something to silently paper over. **Scores_Current is the one exception — it writes with `typecast=True`** (changed 2026-08-02, so a new Group/Subgroup auto-creates its option instead of failing the run; see Section 9). That exception does *not* remove the need to create the six `Decision` options by hand: `typecast` can invent a missing *option*, but it cannot invent a missing *field*, and relying on it to auto-create decision states would silently mask a typo in `DECISION_STATES` as a brand-new state rather than surfacing it. `main.validate_decision_fields()` closes that hole explicitly — see the next entry.

**`main.py`** — orchestrates the above per symbol: pull → score → **classify into a decision state** → build the Indicators/Scores_Current row shapes (and the history-log rows) → (if `--write`) upsert Indicators/Scores_Current to Airtable, append to `history_log.jsonl`, and generate `dashboard_data.json`. Things worth calling out:
- **Where the decision forward pass runs**: `run()` calls `decision.add_decision_series(scored)` immediately after `scoring.score_series(...)`, inside the same per-symbol `try`/`except`, once per symbol over that symbol's full ~2-year scored history. Everything downstream reads the resulting `Decision` column: `build_scores_current_row()` takes the latest row's value for `Decision`, `build_checkpoint_fields()` takes `Decision_1W/2W/3W/1M` by nearest-prior-date lookup (the same mechanism and the same `_nearest_snapshot_on_or_before` helper as the existing TechScore/regime checkpoints), `build_history_log_rows()` writes each row's own point-in-time value, and `DASHBOARD_JSON_FIELDS` carries all five into `dashboard_data.json`. Because it's recomputed in memory from the same EODHD pull every run, the decision history needs no accumulated snapshots and a newly-added symbol gets fully-populated checkpoints on its first run.
- **History-log bootstrap-then-append logic**: `history_log.jsonl` (repo root, not Airtable — see Section 3) is governed by one simple rule: if the file doesn't exist yet, the very first run writes every scoreable date across the full ~2-year EODHD pull for every processed symbol (free to do, since that data is already computed in memory for the checkpoint calculations); every run after that just appends the single latest date per symbol. It's a plain append-only log — no dedupe against existing (Symbol, Date) lines, so an occasional duplicate line from a same-day re-run is possible and considered harmless. **Known limitation, by design**: a symbol added to Symbols *after* the initial bootstrap won't retroactively get historical rows in this file — a deliberate simplicity tradeoff, acceptable because nothing in the live pipeline ever reads this file back.
- **Closed-set guard on the decision fields**: `validate_decision_fields()` runs over the assembled Scores_Current rows immediately after the per-symbol loop — before the write summary, before any Airtable call, and on dry runs too. It raises `RuntimeError` (not `assert`, which compiles away under `python -O`) if any of the five `Decision*` values is anything other than one of the six states or `None`. This restores fail-loud behaviour for these fields specifically: because Scores_Current writes with `typecast=True`, a malformed decision string would otherwise be *accepted* and created as a seventh single-select option, silently corrupting a vocabulary meant to be closed at six — and it would recur every run, looking perfectly legitimate in the Airtable UI. Doing it here rather than by turning typecast off base-wide keeps the new-Group/Subgroup auto-create behaviour that typecast was added for. `None` passes, since a checkpoint with no snapshot that far back (or a date too early to have a full smoothing window) legitimately writes blank.
- **Dry-run safety gate**: `python main.py` alone only computes and prints a summary — it never touches Airtable or the history log. You must pass `--write` to actually perform writes. The daily GitHub Actions workflow always passes `--write`.
- `dashboard_data.json` is written **only** on a real `--write` run, and only after the Airtable writes succeed, so it's guaranteed to reflect the same state just pushed to Airtable — never a locally-computed-but-unwritten state. `history_log.jsonl` is appended under the same `--write` gate.

**Not part of the daily pipeline** (each file says so in its own docstring): `smoke_test.py`, `scoring_check.py`, `checkpoint_check.py`, `momentum_check.py`. These are manual debugging scripts kept around for future troubleshooting (EODHD connectivity, score sanity-checking, checkpoint retrieval, and Improving/Deteriorating firing-rate checks, respectively). They're safe to ignore for normal operation.

---

## 6. Scheduling & Automation

Verified from [.github/workflows/daily-run.yml](.github/workflows/daily-run.yml):

- **The daily trigger is external, not GitHub's `schedule:` cron.** GitHub's own scheduled-workflow queue was empirically landing runs **7.5–12.5 hours late** every day (scheduled runs are deprioritized and queue behind contention; moving the cron minute earlier — the old `11 0 * * *` — didn't help). So on 2026-08-02 the `schedule:` trigger was **removed entirely**, and the workflow now runs on **`workflow_dispatch` only**.
- **What drives it now**: a **Make.com scenario** fires once a day at a fixed local time and calls this workflow's dispatch API (`POST /repos/{owner}/{repo}/actions/workflows/daily-run.yml/dispatches` with `{"ref":"main"}`, authenticated by a fine-grained GitHub PAT stored in Make). Dispatched runs start **immediately** instead of queueing, which is the whole reason for the switch. The PAT is scoped to just Actions on this one repo and expires yearly (rotate before then).
- **The cadence is Monday to Friday only — no weekend runs.** Observed dispatch time is **~23:43 UTC**, consistently within a ~3-second band day to day. ⚠️ **This is the one operational fact that exists nowhere in this repo**: `daily-run.yml` has no `schedule:` block at all (it's `workflow_dispatch` only), so both the time *and* the weekday restriction live entirely inside the Make.com scenario, which can't be inspected from the codebase or the GitHub API. It is recorded here because there is nowhere else to record it. Practical consequence: a weekend gap in the Actions run history is **expected**, not a missed run — see §8.
- **Trade-off introduced**: the daily trigger now depends on a third-party service (Make) and a long-lived credential, rather than being fully self-contained in GitHub. Accepted in exchange for reliable timing. If Make is ever dropped, re-add a `schedule:` block to restore a (late-but-free) GitHub-native trigger.
- **`workflow_dispatch`** also still allows manual runs from the Actions tab or `gh workflow run daily-run.yml`.
- **The commit-back step**: after `python main.py --write` runs, a second step configures a `github-actions[bot]` git identity, checks whether `dashboard_data.json` and/or `history_log.jsonl` actually changed (`git status --porcelain` on both paths — `git status`, not `git diff`, because `history_log.jsonl` doesn't exist as a tracked file at all until its first-ever run creates it), and if so commits both with `[skip ci]` (so the commit doesn't re-trigger anything) and pushes to `main` — with one `pull --rebase` + retry if the push is rejected by a same-minute race. This step runs `if: always()`, so even a partially-failed pipeline run (some symbols errored) still commits whatever data was successfully written.
- **Alerting**: there's no custom notification system (no Slack webhook, no email step). Because the trigger is now `workflow_dispatch` rather than `schedule`, GitHub's scheduled-run failure email no longer applies; instead GitHub notifies **the account that triggered the run** on failure. The dispatch is authenticated by *your own* fine-grained PAT (the runs show `manpat2426` as actor), so under GitHub's default "notify on failed workflows only" setting, failure emails should still reach you. Had the PAT belonged to a machine account, those emails would have gone there instead — that's the failure mode this arrangement avoids. ⚠️ **The mechanism is verified; the delivery is not.** The dispatch-only trigger and the `manpat2426` actor attribution were both confirmed from the live Actions API, but whether the emails actually arrive depends on account-level notification settings that aren't visible through the API. There were real `workflow_dispatch` failures on 2026-08-01 and 2026-08-02 — checking whether those generated emails is the cheap way to confirm this end to end. Note that Make.com is **not** a failure channel: the dispatch API returns `204 No Content` meaning only "run queued," so Make shows success even for a run that later fails — don't rely on it. This all still depends on your GitHub notification settings being at their defaults.

---

## 7. Dashboard Hosting

- `index.html` is a single self-contained file (inline CSS/JS, no build step, no framework). On load, it does `fetch("dashboard_data.json")` — a plain relative path, so it only ever works when served alongside that JSON file.
- **Served via GitHub Pages**, confirmed live: source is the `main` branch, path `/` (repo root) — i.e., Pages serves the same repo the pipeline commits to, so a successful daily commit is what keeps the live site current.
- **Live URL**: **https://manpat2426.github.io/technical-dashboard/**
- **The decision columns are rendered on the dashboard** (shipped 2026-08-09; verified against the committed `index.html`, not against the original spec). `dashboard_data.json` carries `Decision`, `Decision_1W`, `Decision_2W`, `Decision_3W`, and `Decision_1M` for every symbol, and `index.html` now displays all five as a **single 5-column "Decision" group that leads the table** — immediately after the sticky Symbol/Name pair and *ahead of* the TechScore block, in the order `Now, 1W, 2W, 3W, 1M`. The full group order is now `Decision(5) · TechScore(7) · Current Regime(3) · Trend(4) · Momentum(4) · Volatility(4)`, 29 columns in total. Decision leads because it's the conclusion the rest of the row supports; it also means the decision is readable on a phone without scrolling sideways at all.
  - **Colour** reuses the existing 7-step diverging scale as a categorical `state → step` lookup (`DECISION_STEP` in `index.html`, sitting alongside the `TREND_STEP`/`MOMENTUM_STEP`/`VOLATILITY_STEP` tables) rather than the numeric `techScoreStep()` function: `Steady-constructive` → step 7 (deepest green), `Improving` → 6 (a lighter green — the two are kept a step apart so "hold" and "add" are scannable apart, and Steady-constructive takes the deeper one because it only ever arises from the High band), `Neutral / No Signal` → 4 (yellow), `Concerning` → 3 (orange), `Steady-weak` → 2 (red), `Exit` → 1 (deepest red). No new palette or CSS variables were introduced.
  - **Reuse over reinvention**: decision columns are `kind: "decision"` and fall through the *existing* regime cell path for rendering, sorting, blank-handling, and group-boundary borders. The only new CSS is a width class (132px desktop / 112px mobile, versus the regime columns' 108px/90px) so the two longest state strings — `Steady-constructive` and `Neutral / No Signal` — fit without ellipsis.
  - **A compact legend** was added to the footer: the six states with a one-line meaning each. Its chips are painted from the same `DECISION_STEP` lookup and the same `.step-N` classes as the table cells, so a legend colour cannot drift from its column. Under 640px the meanings collapse and only the coloured chips remain.
  - The existing filter buttons, sticky Symbol/Name columns, sticky header, and mobile/collapsed-filter layout were all preserved and re-verified after the change. Blank checkpoints (a symbol too early in its history, or a newly-added one) render as empty cells, never `undefined` — though note that as of this writing **no symbol actually has a blank checkpoint**, so that path was verified with deliberately doctored data rather than live data.
- Because the repo is public, Pages hosting is free, and because `index.html` never touches Airtable directly, nothing sensitive is exposed by making the site (and the repo) public.

---

## 8. Troubleshooting / Known Issues

- **Weekends: there is no weekend run, and a weekend gap is not a fault.** The Make.com dispatch fires **Monday to Friday only** (see §6) — no Saturday, no Sunday. So an empty stretch in the Actions history across a weekend is expected and correct; only a missing *weekday* run is a real signal. When auditing run cadence, count weekdays, not calendar days. The visible side effect is that `dashboard_data.json`'s `last_updated` reads two to three days old all weekend — also normal, and not evidence of a stale pipeline. (Nothing would be gained by running anyway: markets are closed, so EODHD would just return the same Friday close.)
- **Non-trading weekdays** (market holidays like Thanksgiving or July 4th) *do* still get a run, since the dispatch is weekday-based rather than market-calendar-aware. EODHD returns data through the last actual trading day, so "latest" resolves to the prior session's close — Indicators/Scores_Current are simply rewritten with the same values (harmless, since they're replaced in place, not appended). `history_log.jsonl`, however, has no dedupe (see Section 5), so a run that doesn't advance to a new trading day appends a duplicate-looking (Symbol, same Date) line. Expected and harmless for an audit log, but worth knowing if you ever use line-count-per-symbol as a proxy for trading days. *(The log currently holds exactly one such cluster — 117 duplicate lines all dated 2026-07-31 — left over from the 2026-08-01/08-02 weekend runs during the changeover to the Make.com trigger, before the Mon–Fri cadence settled. Under the current schedule this can only recur on a weekday holiday or a same-day manual re-run.)*
- **Timing is now reliable via Make.com dispatch — this was the whole reason for the switch (§6).** Dispatched runs start within seconds of being triggered, so the pipeline now runs at its intended fixed time (~23:43 UTC, a few hours after the US close) rather than whenever GitHub's scheduled queue got around to it. The `last_updated` stamp on the dashboard reflects that prompt evening run.
- **Historical context — why the `schedule:` cron was abandoned.** Before the 2026-08-02 switch, the pipeline ran on GitHub's built-in `schedule:` cron, and scheduled runs were landing **9–12 hours late every day** because GitHub deprioritizes scheduled workflows and they queue behind contention. The run history at the time (`gh run list`) made this concrete:

  | Date (2026) | Cron fire time | Actual run started |
  |---|---|---|
  | 07-20 | 00:11 UTC | 12:00 UTC (~11h 49m late) |
  | 07-21 | 00:11 UTC | 10:34 UTC (~10h 23m late) |
  | 07-22 | 00:11 UTC | 10:35 UTC (~10h 24m late) |
  | 07-23 | 00:11 UTC | 10:36 UTC (~10h 25m late) |
  | 07-24 | 00:11 UTC | 10:28 UTC (~10h 17m late) |
  | 07-25 | 00:11 UTC | 09:14 UTC (~9h 3m late) |
  | 07-26 | 00:11 UTC | 09:37 UTC (~9h 26m late) |

  Moving the cron minute earlier didn't help (the delay is queue contention, not the fire time), so the `schedule:` trigger was removed entirely in favor of the Make.com dispatch. GitHub gives no SLA on scheduled-run start times; dispatched runs are not subject to the same deprioritization. This table is kept only as the record of *why* the switch was made — it does not describe current behavior.
- **"Commit the script, or the dashboard goes stale."** Two related traps:
  1. If you edit any `.py` file locally and test it, but don't `git push` to `main`, GitHub Actions keeps running the **old** version every night. The live dashboard will look "stuck" even though your local testing shows different behavior — there's no drift-detection between local and deployed code.
  2. If you run `python main.py --write` locally with real credentials, your local `dashboard_data.json` changes, but nothing pushes it anywhere. Only the GitHub Actions commit-back step updates the file that GitHub Pages actually serves. A local `--write` run is useful for testing, but doesn't affect the live site unless you also commit and push that file (and normally you shouldn't — let the daily run own it, to avoid racing the bot's own commit).
- **BRK-B ticker-format quirk**: confirmed live in the Symbols table — Berkshire Hathaway's B shares are stored as `BRK-B.US`, i.e. EODHD keeps the literal hyphen from the share-class suffix rather than using a dot (it is *not* `BRK.B.US` or `BRKB.US`). Worth remembering when adding any other hyphenated share-class ticker (e.g. `BF-B`) — get the exact EODHD format right, or the EOD/technical pulls for that symbol will 404 or error out (caught per-symbol by `main.py`'s try/except, so it won't take down the whole run, but that symbol will be skipped).
- **3 blank rows in the live Config table** — harmless (silently skipped by `get_config()`), but worth deleting for tidiness next time you're in there.

---

## 9. Maintenance & Operations

**Adding a security**: add a row to Symbols with the correct EODHD-format `Symbol`, a `Name`, a `Group`, optionally a `Subgroup`, and check `Active`. The next scheduled (or manually dispatched) run will pick it up automatically via `get_active_symbols()`, pull `HISTORY_YEARS` (2 years) of history, score it, and populate its Scores_Current checkpoint columns immediately — they're computed in-memory from that same 2-year pull, not from any accumulated snapshot history, so there's no waiting period (see Section 5). Note: it will **not** retroactively appear in past `history_log.jsonl` entries — per Section 3's noted limitation, it only starts showing up there from the day it's added onward.

**Adding a brand-new Group or Subgroup**: just type the new value into the Symbols row's Group/Subgroup dropdown — no other step needed. `upsert_scores_current` writes Scores_Current with `typecast=True`, so a Group/Subgroup value that doesn't yet exist as an option in the Scores_Current table is **auto-created** on the next run rather than failing it. (This was a real gotcha before 2026-08-02: Scores_Current has its own single-select dropdowns separate from Symbols, and a new value used to fail the whole run with a 422 `INVALID_MULTIPLE_CHOICE_OPTIONS` until you manually added the option. `typecast=True` removed that manual step. The dashboard filter buttons were never the issue — those are already dynamic; see the "New Subgroups" note below.) One caveat inherited from typecast: a *typo* in a Group/Subgroup (e.g. "Alterntaives") will also be silently auto-created as a new category rather than caught, so double-check spelling when adding one.

**Removing a security**: uncheck `Active` in Symbols rather than deleting the row — `get_active_symbols()` filters on `Active`, so an inactive symbol simply stops being processed (its existing Indicators/Scores_Current rows are left alone, not deleted, and it simply stops appearing in future `history_log.jsonl` appends).

**New Subgroups — correction to a common assumption**: the dashboard's Group+Subgroup filter buttons are **not hardcoded**. `index.html`'s `buildFilters()` derives the full list of combo buttons dynamically from whatever `Group`/`Subgroup` pairs actually appear in `dashboard_data.json` at load time ([index.html:554-577](index.html:554)). So adding a **new Subgroup** under an existing Group (e.g., a new sector) needs **no dashboard code change at all** — it appears automatically after the next run. The only two filter buttons that *are* hardcoded are the top-level "Stocks" and "ETFs" buttons (tied to the literal string values of `Group`); a genuinely new top-level Group (a third one, beyond Stocks/ETFs) would need a small `index.html` edit to add its button.

**Tuning the model**: most thresholds and periods live in the Config table and take effect on the very next run — no code change, no deploy, edit the `Value` cell and you're done. **Two exceptions, both in code rather than Config**: (1) the Momentum-regime numeric cutoffs (RSI ≥60/≥50/≤40/<50, the MACD/ROC sign checks, and the Improving/Deteriorating constants — the ~5-trading-day lookback, the ±3 RSI points, the ±0.02 ROC move) are hardcoded constants inside `scoring.py` — see the discrepancy note in the next section; and (2) the entire decision layer's eight parameters, which live in the labeled constants block at the top of `decision.py`. Tuning either set means editing that file, committing, and pushing. The `decision.py` block is the better-organised of the two — one block, one place, commented — and is the model to follow if the `scoring.py` constants are ever tidied up.

**Changing a decision state name**: the six strings in `decision.py`'s `DECISION_STATES` must match the single-select options on all five `Decision*` fields in Scores_Current exactly. Renaming one in code without renaming the Airtable option on all five fields will fail those writes.

**How the daily refresh works, and how to tell if it broke**: check the Actions tab. A green run means every symbol scored and wrote successfully. A red run means `main.py` exited non-zero — look at that run's log for the `FAILING: N symbol(s) errored` line and the per-symbol error messages just above it (usually an EODHD pull failure for one ticker). Because the commit-back step runs `if: always()`, a red run can still have updated `dashboard_data.json` with everything that *did* succeed — a red X means "go look," not necessarily "the site is stale." You should also get a failure email — now via GitHub's actor-notification on the dispatched run rather than the old scheduled-run email (see §6 Alerting), assuming your account's notification settings haven't been changed from default.

**Operating practice**: this project is maintained across short, focused Claude Code sessions rather than one long-running one — a fresh session per task, committing frequently so GitHub (not chat history) is the durable source of truth, and leaning on `METHODOLOGY.md` + this file to bring a new session (or another person, or another LLM) up to speed without re-deriving context from scratch.

---

## 10. Limitations & Future Enhancements

- **Resolved: Scores_History was removed from Airtable entirely on 2026-07-27.** It had grown to 15,142 rows and was the sole reason the base needed a paid Airtable plan (the other four tables total only ~363 records, comfortably under the free 1,000/base limit). History now lives in `history_log.jsonl` in the repo instead (see Section 3). That file has no comparable ceiling to manage — git repo size, not Airtable records, is the only long-run constraint, and at ~113 lines/trading-day (~28,000/year) that's a few MB/year of plain text, not a practical concern for the foreseeable future.
- **Considered but not built: a daily success confirmation.** Right now the only email signal is a *failure* email from GitHub's defaults; there's no positive "yes, it ran and wrote 113 rows" ping. A silent success and GitHub Actions simply not firing can currently look identical from your inbox. A small addition (e.g., a final workflow step that pings a webhook or sends a one-line success email) has been discussed as a future enhancement but isn't implemented.
- **Resolved: the decision columns are now rendered on the dashboard.** The current-`Decision` column and the four `Decision_1W/2W/3W/1M` checkpoints display on the live site, color-coded on the shared green→red scale, with filters/sticky-columns/mobile layout preserved (see Section 7). The decision data was already flowing to Airtable, `history_log.jsonl`, and `dashboard_data.json`; this was the front-end step that surfaces it.
- **The decision layer's parameters have not been tuned against live data yet.** They're construction-based absolute values chosen up front, not fitted to the observed distribution. A spot check across 7 symbols over ~343 decided days each showed all six states firing, with the official decision changing on ~6% of days (roughly one state change every 16 trading days) — stable rather than chattery, which is the intent. Two things to watch if the output ever feels off: `MID_BAND_SLOPE = 2` is a demanding bar for a 15-day rolling mean (it needs the last 5 days to average ~6 points above the 5 they replaced), so `Improving`/`Concerning` in the Middle band are relatively rare; and because the week-over-week slope requires all three steps (now→1W→2W→3W) to agree, a security that rockets from Middle to High in under three weeks can skip `Improving` and land straight on `Steady-constructive`. Both follow the design as specified; both are single-constant changes in `decision.py` if they want loosening.
- **Resolved: `numpy` is now a declared dependency.** `decision.py` does `import numpy as np`, but `requirements.txt` originally listed only `requests`, `python-dotenv`, and `pandas` — so the import worked purely because pandas pulls numpy in transitively. That never failed in practice, but relying on a transitive dependency for a direct import is fragile: a future pandas that changed how it vendors numpy would have broken the run at *import time*, before any of the pipeline's own error handling (including the decision guard) could apply. `numpy` was added to `requirements.txt` on 2026-08-09, unpinned to match the file's existing style. The general rule this is an instance of: **if a module `import`s it, it belongs in `requirements.txt`**, whether or not something else happens to install it.
- **No automated tests**: the four `*_check.py` scripts are manual/eyeball debugging aids, not an automated test suite — there's no CI step that runs them or asserts on their output. The decision layer was verified during its build (synthetic series exercising every band, hysteresis, and the persistence rule; a manual recompute confirming `sTech` is a correctly-aligned trailing mean; a prefix-vs-full-history comparison confirming no lookahead; and a real Airtable write that round-tripped all five `Decision*` fields) — but none of that was kept as a committed test.
- **The "~17% of days" Improving/Deteriorating firing-rate figure in METHODOLOGY.md §6 could not be verified** from the code alone — see the discrepancy note below.

### Cross-check: METHODOLOGY.md numeric claims vs. the live Config table and scoring.py

I compared every specific number in `METHODOLOGY.md` against the live Config table (21 real parameters) and `scoring.py`. Result: **everything checked out exactly**, with two things worth flagging:

1. **Not every threshold actually lives in Config, despite the blanket claim in METHODOLOGY.md's opening note** ("wherever specific numeric thresholds or parameters appear... the authoritative source is the Config table"). All **Trend** thresholds (`PRICE60_HIGH`=1.05, `PRICE60_LOW`=0.95, `PRICE120_HIGH`=1.10, `PRICE120_LOW`=0.90) and all **Volatility**/indicator-period parameters (`ATR_Period`=20, `ATR_Baseline_Period`=90, `BB_Period`=20, `BB_BBW_Period`=125, `RSI_Period`=14, `ROC_Period`=20, `MACD_Fast`=12, `MACD_Slow`=26, `MACD_Signal`=9) are genuinely in Config and match METHODOLOGY.md exactly. But the **Momentum regime cutoffs** (RSI ≥60/≥50/≤40/<50, the MACD/ROC sign checks) and the **Improving/Deteriorating tuning constants** (the ~5-trading-day lookback, ±3 RSI points, ±0.02 ROC) are hardcoded directly in `scoring.py`, not read from Config at all. Recommend either softening that opening note to carve out this exception, or actually moving those constants into Config if runtime tunability for them is wanted.
2. **The "~17% of days" firing-rate figure** for Improving/Deteriorating after the all-three-conditions tightening (METHODOLOGY.md §6) doesn't appear anywhere in the code or its comments — `scoring.py`'s own docstring only documents the *pre*-tightening rate ("~30–39% of dates," which matches METHODOLOGY.md's "roughly a third" claim). The post-tightening 17% figure would need to be reproduced by actually running `momentum_check.py` (or equivalent) against live data — I can't confirm or deny it from static code alone.

Everything else — the MomentumAdj mapping (+3 to −3), the VolAdj mapping (+2 to −2), and all indicator periods — matches the live system exactly.

---

## 11. Design Rationale (Distilled)

**Why Airtable over a plain spreadsheet?** The model needs typed, validated fields (single-select regimes that reject typos, checkboxes, dates) across five related tables, plus a REST API a script can read and write programmatically without any spreadsheet-specific SDK. A spreadsheet can hold numbers, but it can't natively enforce "this cell must be one of these seven regime strings" or give a script a stable table/field ID to write to — Airtable does both while still being as easy for a human to browse and hand-edit as a spreadsheet.

**Why does the compute live in a scheduled cloud script instead of running locally?** So the daily refresh doesn't depend on a personal machine being on, connected, and remembered. GitHub Actions is free for this public repo, already hosts the code, and keeps the "when did this last run and did it succeed" history in one auditable place (the Actions tab) instead of a local terminal's scrollback.

**Why a custom dashboard instead of a no-code tool (e.g., an Airtable Interface)?** The dashboard's UX — a sticky header and sticky Symbol/Name columns that survive both-axis scrolling, a shared 7-step diverging color scale across every score and regime column, and a mobile layout that collapses filters into a toggle panel — is the kind of pixel-level behavior that's hard to get exactly right in a no-code builder. Building it as a single static HTML file also means the public artifact carries **zero credentials**: it fetches a plain JSON file, never Airtable directly, which is what makes it safe to host on a public URL in the first place.

**Why fully-automated cloud refresh instead of running it manually?** Consistency (same schedule every day, not "whenever someone remembers") and resilience (the dry-run-by-default CLI, per-symbol error isolation so one bad ticker doesn't kill the whole run, and the `if: always()` commit-back step so partial progress is never lost) all only pay off if the thing actually runs unattended, every day, without a human in the loop.

---

*End of deployment guide. For the model's methodology — what each indicator measures and why, how scores and regimes are built, and how to read the dashboard — see `METHODOLOGY.md`.*
