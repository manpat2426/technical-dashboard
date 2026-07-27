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
      │     (Config & Symbols are read-only inputs; Indicators,
      │      Scores_Current, and Scores_History are written)
      │
      └──▶ writes dashboard_data.json into the repo, then the SAME
            workflow run commits that file back to `main`
                  │
                  ▼
      GitHub Pages serves the repo's static files (index.html + dashboard_data.json)
                  │
                  ▼
      Browser loads index.html, which fetches dashboard_data.json client-side
```

**The core design principle: methodology lives in the script, Airtable stores and displays everything, and the public dashboard reads a static JSON snapshot — never Airtable directly.**

- All scoring logic, thresholds-that-are-actually-tunable, and regime rules live in the Python files in this repo (`scoring.py` above all). Airtable is not a spreadsheet-with-formulas; it's a **record store and viewer**. This keeps the methodology in one traceable, version-controlled place instead of split between code and hidden Airtable formulas.
- Airtable holds every input and every intermediate/derived value (see Section 3), so a human can inspect *why* a score came out the way it did without re-running anything.
- The dashboard (`index.html`) never talks to Airtable and carries no Airtable token. It only ever fetches `dashboard_data.json`, a plain static file sitting next to it in the same GitHub Pages deployment. This is what makes it safe to make the repo (and therefore the dashboard) public — there is no credential-bearing code running in the browser.

---

## 2. Accounts, Services & Costs

| Service | Role | Verified details | Cost |
|---|---|---|---|
| **EODHD** | Market data API (EOD prices + technical indicators) | API key stored as a GitHub Actions secret (`EODHD_API_KEY`). Code comments in [config.py](config.py:43) document an assumed budget of **100,000 credits/day** and an assumed **~1,000 requests/minute** cap, with each *technical* endpoint call costing 5 credits (EOD price calls are not stated to cost extra). A full run pulls 1 EOD + 6 technical series per symbol × 113 symbols ≈ 800 requests/day, of which 6×113=678 are "technical" (5 credits each) ≈ **3,390 technical credits/day** — comfortably inside the assumed 100,000/day budget. | ⚠️ **Unverified** — the actual EODHD subscription tier and its monthly price aren't recorded anywhere in the repo or accessible to me. Confirm directly on your EODHD account dashboard. |
| **GitHub** | Hosts the repo, runs the daily pipeline (Actions), serves the dashboard (Pages) | Repo `manpat2426/technical-dashboard` is confirmed **public**. Actions minutes and Pages hosting are free for public repositories. | **$0/month** (verified — public repo) |
| **Airtable** | Stores Config/Symbols/Indicators/Scores_Current/Scores_History | Base `appummriRwPGUNjsj`, 5 tables. `Scores_History` alone currently holds **15,142 records** (verified live) and grows daily (see Section 10). | ⚠️ **Unverified** — I can't see billing/plan info through the Airtable API. A base with 15,000+ records in a single table is past Airtable's free-tier per-base record ceiling, so this account is very likely on a paid plan (Team/Business or similar) — confirm the exact tier and price on your Airtable account page. |

**Monthly cost tally:** GitHub $0 (confirmed). EODHD and Airtable costs need to be filled in from your own account billing pages — flagging rather than guessing.

---

## 3. The Airtable Schema (verified live, 2026-07-26)

Base: `appummriRwPGUNjsj`. Five tables, exactly as `airtable_client.py` documents.

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
| TechScore_1W / 2W / 3W / 1M / 2M / 3M | Number | Historical checkpoints |
| TrendRegime_1W / 2W / 3W / 1M | Single select | |
| MomentumRegime_1W / 2W / 3W / 1M | Single select | |
| VolatilityRegime_1W / 2W / 3W / 1M | Single select | |

### Scores_History
*Dated snapshots of the full technical state, one row per symbol per date. Backfilled on first add, then appended every run. Powers the checkpoint columns above via nearest-prior-date retrieval.*

| Field | Type | Notes |
|---|---|---|
| Symbol | Single line text | Primary field |
| Group | Single line text | **Plain text here**, unlike Scores_Current's single-select Group — snapshot-time value, not a live-filterable field |
| Date | Date | Snapshot date |
| TrendScore, MomentumAdj, VolAdj, TechScore | Number | |
| TrendRegime, MomentumRegime, VolatilityRegime | Single select | |
| SnapshotType | Single select | e.g. `Backfill` |
| Notes | Long text | Optional |

Currently **15,142 records**, verified live (see Section 10 for growth implications).

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

**`config.py`** — loads `.env`, fails loudly if secrets are missing, and defines structural constants: the Airtable base/table IDs, the two EODHD URL templates, the rate-limit pause (0.4s between EODHD calls), `TEST_MODE`/`TEST_SYMBOLS` (currently `TEST_MODE = False`, so a full run processes all active Symbols), `HISTORY_YEARS = 2`, and `BACKFILL_MONTHS = 6`. Model *parameters* (MA periods, thresholds) are deliberately **not** here — see Section 3's Config table.

**`eodhd_client.py`** — thin HTTP wrapper. `get_eod(symbol)` pulls ~2 years of daily prices, using EODHD's `adjusted_close` field (**split- and dividend-adjusted**, not raw close). `get_technical(symbol, function, period)` pulls one technical series at a time (`sma`, `atr`, `rsi`, `bbands`, `macd`), also computed by EODHD on adjusted data. Reuses one `requests.Session()` across all calls — the file's own comment notes this cuts the dominant cost of a full run (TCP/TLS handshake overhead) from ~1.1–1.3s to ~0.1–0.25s per request.

**`indicators.py`** — `build_symbol_history(symbol, config)` calls the EODHD client for EOD prices plus SMA(60), SMA(120), ATR, RSI, Bollinger Bands, and MACD (periods read from the live `config` dict, not hardcoded), then outer-merges them all into one date-sorted DataFrame. This is the one place that translates EODHD's field names (`uband`, `signal`, etc.) into the pipeline's internal column names.

**`scoring.py`** — the model itself:
1. `compute_derived_series()` adds `ATR90Avg` (rolling mean of ATR20), `ROC20` (Close vs. Close N periods ago), `BBW` (normalized Bollinger Band width), `BBW_Percentile` (vectorized rolling percentile rank), `ATR_Ratio` (ATR20/ATR90Avg), and the "5 trading days ago" prior-value columns used by the Improving/Deteriorating regimes.
2. `score_row()` computes Price60/Price120, MAAlign, the Trend/Momentum/Volatility scores and regimes, and the final `TechScore` for one date, reading thresholds from `config`.
3. `score_series()` runs `score_row()` across every date that has a complete lookback (drops early dates lacking enough history for MA120/ATR90Avg/BBW's 125-day window).

**`airtable_client.py`** — all Airtable I/O, via the raw REST API (no SDK dependency). Config and Symbols are read-only. Indicators and Scores_Current are **replaced in place, one row per symbol** on every run. Scores_History is **upserted keyed by (Symbol, Date)** — re-running or re-backfilling never creates duplicate snapshot rows. `typecast=False` on every write, on purpose: a rejected write means a genuine mismatch (e.g. a regime string that doesn't match an existing single-select option) worth seeing, not something to silently paper over.

**`main.py`** — orchestrates the above per symbol: pull → score → build the three Airtable row shapes → (if `--write`) upsert to Airtable → generate `dashboard_data.json`. Two things worth calling out:
- **Backfill-then-append history logic**: on every run, `backfill_window()` takes the trailing `BACKFILL_MONTHS` (6) of already-scored dates and upserts all of them to Scores_History, tagged `SnapshotType=Backfill`. Because writes are keyed by (Symbol, Date), this is safe to run daily — a brand-new symbol gets ~6 months of history populated immediately (so its checkpoint columns aren't blank for months), and an existing symbol just re-upserts the same recent window (a no-op for already-written dates, a genuine append for the new date).
- **Dry-run safety gate**: `python main.py` alone only computes and prints a summary — it never touches Airtable. You must pass `--write` to actually perform writes. The daily GitHub Actions workflow always passes `--write`.
- `dashboard_data.json` is written **only** on a real `--write` run, and only after the Airtable writes succeed, so it's guaranteed to reflect the same state just pushed to Airtable — never a locally-computed-but-unwritten state.

**Not part of the daily pipeline** (each file says so in its own docstring): `smoke_test.py`, `scoring_check.py`, `checkpoint_check.py`, `momentum_check.py`. These are manual debugging scripts kept around for future troubleshooting (EODHD connectivity, score sanity-checking, checkpoint retrieval, and Improving/Deteriorating firing-rate checks, respectively). They're safe to ignore for normal operation.

---

## 6. Scheduling & Automation

Verified from [.github/workflows/daily-run.yml](.github/workflows/daily-run.yml):

- **Cron**: `11 0 * * *` → **00:11 UTC**, every day. That's **7:11 PM EST** (winter) or **8:11 PM EDT** (summer) — roughly 3–4 hours after the 4:00 PM ET market close.
- **Why :11 and not the top of the hour**: the workflow's own comment explains that exact-hour-boundary schedules (`0 2 * * *`, etc.) were empirically observed to queue behind heavy GitHub Actions contention and run 6–10 hours late; offsetting to minute 11 was the fix attempted.
- **`workflow_dispatch`** is also enabled, so the pipeline can be triggered manually from the Actions tab (used for the ad-hoc verification run captured below).
- **The commit-back step**: after `python main.py --write` runs, a second step configures a `github-actions[bot]` git identity, checks whether `dashboard_data.json` actually changed (`git diff --quiet`), and if so commits it with `[skip ci]` (so the commit doesn't re-trigger anything) and pushes to `main` — with one `pull --rebase` + retry if the push is rejected by a same-minute race. This step runs `if: always()`, so even a partially-failed pipeline run (some symbols errored) still commits whatever data was successfully written.
- **Alerting**: there's no custom notification system (no Slack webhook, no email step). The only alerting is **GitHub's own default behavior of emailing the repo owner when a scheduled workflow run fails** — this depends on your GitHub notification settings being at their defaults; it isn't something this repo configures or that I can verify from here.

---

## 7. Dashboard Hosting

- `index.html` is a single self-contained file (inline CSS/JS, no build step, no framework). On load, it does `fetch("dashboard_data.json")` — a plain relative path, so it only ever works when served alongside that JSON file.
- **Served via GitHub Pages**, confirmed live: source is the `main` branch, path `/` (repo root) — i.e., Pages serves the same repo the pipeline commits to, so a successful daily commit is what keeps the live site current.
- **Live URL**: **https://manpat2426.github.io/technical-dashboard/**
- Because the repo is public, Pages hosting is free, and because `index.html` never touches Airtable directly, nothing sensitive is exposed by making the site (and the repo) public.

---

## 8. Troubleshooting / Known Issues

- **Weekends / non-trading days**: the script runs on the same daily cron regardless of whether the market was open. EODHD simply returns data through the last actual trading day, so "latest" just resolves to Friday's close on a Saturday/Sunday run. Because Scores_History writes are keyed by (Symbol, Date), a run on a non-trading day doesn't create a duplicate or garbage row — it just re-upserts the same most-recent date, a no-op.
- **Winter (EST) timing margin is thin**: the workflow comment is explicit about this — 00:11 UTC is only ~11 minutes past the "7:00 PM ET floor" it was originally targeting, in winter. If EODHD's post-close data update ever lags on a given day, this schedule has very little cushion. (In summer/EDT there's an extra hour of margin.)
- **GitHub Actions scheduled-run timing variability — confirmed, and currently worse than the workflow's own comment anticipates.** I pulled the actual run history (`gh run list`) rather than assuming the cron fires on time:

  | Date (2026) | Scheduled | Actual run started |
  |---|---|---|
  | 07-20 | 00:11 UTC | 12:00 UTC (~11h 49m late) |
  | 07-21 | 00:11 UTC | 10:34 UTC (~10h 23m late) |
  | 07-22 | 00:11 UTC | 10:35 UTC (~10h 24m late) |
  | 07-23 | 00:11 UTC | 10:36 UTC (~10h 25m late) |
  | 07-24 | 00:11 UTC | 10:28 UTC (~10h 17m late) |
  | 07-25 | 00:11 UTC | 09:14 UTC (~9h 3m late) |
  | 07-26 | 00:11 UTC | 09:37 UTC (~9h 26m late) |

  Every one of the last 7 scheduled runs landed **9–12 hours** after the nominal 00:11 UTC fire time — worse than the "6–10 hours late" the workflow's comment cites as the reason it moved off the top of the hour in the first place. In practice this means the pipeline is actually executing mid-morning UTC (roughly 4–8 AM US Eastern), not the evening-of-close window it was designed for. This isn't currently causing data problems (EODHD's prior-day close is long since settled by then), but it does mean the dashboard's "freshest" update lands much later than the schedule implies — worth knowing if you're ever wondering why `last_updated` looks stale first thing in the morning. GitHub Actions gives no SLA on scheduled-run start times, especially for lower-priority scheduled (vs. manually-dispatched or push-triggered) runs.
- **"Commit the script, or the dashboard goes stale."** Two related traps:
  1. If you edit any `.py` file locally and test it, but don't `git push` to `main`, GitHub Actions keeps running the **old** version every night. The live dashboard will look "stuck" even though your local testing shows different behavior — there's no drift-detection between local and deployed code.
  2. If you run `python main.py --write` locally with real credentials, your local `dashboard_data.json` changes, but nothing pushes it anywhere. Only the GitHub Actions commit-back step updates the file that GitHub Pages actually serves. A local `--write` run is useful for testing, but doesn't affect the live site unless you also commit and push that file (and normally you shouldn't — let the scheduled run own it, to avoid racing the bot's own commit).
- **BRK-B ticker-format quirk**: confirmed live in the Symbols table — Berkshire Hathaway's B shares are stored as `BRK-B.US`, i.e. EODHD keeps the literal hyphen from the share-class suffix rather than using a dot (it is *not* `BRK.B.US` or `BRKB.US`). Worth remembering when adding any other hyphenated share-class ticker (e.g. `BF-B`) — get the exact EODHD format right, or the EOD/technical pulls for that symbol will 404 or error out (caught per-symbol by `main.py`'s try/except, so it won't take down the whole run, but that symbol will be skipped).
- **3 blank rows in the live Config table** — harmless (silently skipped by `get_config()`), but worth deleting for tidiness next time you're in there.

---

## 9. Maintenance & Operations

**Adding a security**: add a row to Symbols with the correct EODHD-format `Symbol`, a `Name`, a `Group`, optionally a `Subgroup`, and check `Active`. The next scheduled (or manually dispatched) run will pick it up automatically via `get_active_symbols()`, pull `HISTORY_YEARS` (2 years) of history, score it, and — because of the backfill logic in `main.py` — immediately populate ~6 months of Scores_History snapshots, so its checkpoint columns aren't blank while you wait for weeks of new data to accumulate.

**Removing a security**: uncheck `Active` in Symbols rather than deleting the row — `get_active_symbols()` filters on `Active`, so an inactive symbol simply stops being processed (its existing Indicators/Scores_Current/Scores_History rows are left alone, not deleted).

**New Subgroups — correction to a common assumption**: the dashboard's Group+Subgroup filter buttons are **not hardcoded**. `index.html`'s `buildFilters()` derives the full list of combo buttons dynamically from whatever `Group`/`Subgroup` pairs actually appear in `dashboard_data.json` at load time ([index.html:554-577](index.html:554)). So adding a **new Subgroup** under an existing Group (e.g., a new sector) needs **no dashboard code change at all** — it appears automatically after the next run. The only two filter buttons that *are* hardcoded are the top-level "Stocks" and "ETFs" buttons (tied to the literal string values of `Group`); a genuinely new top-level Group (a third one, beyond Stocks/ETFs) would need a small `index.html` edit to add its button.

**Tuning the model**: most thresholds and periods live in the Config table and take effect on the very next run — no code change, no deploy, edit the `Value` cell and you're done. **Exception, worth knowing**: the Momentum-regime numeric cutoffs (RSI ≥60/≥50/≤40/<50, the MACD/ROC sign checks, and the Improving/Deteriorating constants — the ~5-trading-day lookback, the ±3 RSI points, the ±0.02 ROC move) are hardcoded constants inside `scoring.py`, not Config parameters. Tuning *those* specifically requires editing `scoring.py`, committing, and pushing — see the discrepancy note in the next section.

**How the daily refresh works, and how to tell if it broke**: check the Actions tab. A green run means every symbol scored and wrote successfully. A red run means `main.py` exited non-zero — look at that run's log for the `FAILING: N symbol(s) errored` line and the per-symbol error messages just above it (usually an EODHD pull failure for one ticker). Because the commit-back step runs `if: always()`, a red run can still have updated `dashboard_data.json` with everything that *did* succeed — a red X means "go look," not necessarily "the site is stale." You should also (per GitHub's default behavior) get an email if a scheduled run fails, assuming your account's notification settings haven't been changed from default.

**Operating practice**: this project is maintained across short, focused Claude Code sessions rather than one long-running one — a fresh session per task, committing frequently so GitHub (not chat history) is the durable source of truth, and leaning on `METHODOLOGY.md` + this file to bring a new session (or another person, or another LLM) up to speed without re-deriving context from scratch.

---

## 10. Limitations & Future Enhancements

- **Scores_History row growth**: **15,142 rows today** (verified live), growing by up to 113 rows per trading day (one per active symbol, upserted so no duplicate growth on non-trading days). At roughly 252 US trading days/year, that's on the order of **~28,000 new rows/year** at the current universe size. Airtable's per-base record ceilings vary by plan (free tiers are in the low thousands; paid tiers scale into the tens or hundreds of thousands) — worth periodically checking this table's size against whatever plan you're actually on (see Section 2's flagged cost item), and considering an archival/pruning strategy for old `Backfill`-tagged rows if it ever approaches a ceiling.
- **Considered but not built: a daily success confirmation.** Right now the only email signal is a *failure* email from GitHub's defaults; there's no positive "yes, it ran and wrote 113 rows" ping. A silent success and GitHub Actions simply not firing can currently look identical from your inbox. A small addition (e.g., a final workflow step that pings a webhook or sends a one-line success email) has been discussed as a future enhancement but isn't implemented.
- **No automated tests**: the four `*_check.py` scripts are manual/eyeball debugging aids, not an automated test suite — there's no CI step that runs them or asserts on their output.
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
