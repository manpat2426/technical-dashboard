# Technical Analysis Dashboard — Methodology

*This document explains the **what** and the **why** of the technical model: what each indicator measures, why it was chosen, how the scores and regimes are built, and how to read the output. It is the companion to `DEPLOYMENT.md`, which covers how the system is built and hosted.*

*A note on precise values: this document explains the reasoning and structure of the model. Most parameters — the trend thresholds, the volatility thresholds, and every indicator's period/lookback — live in the **`Config` table** in the Airtable base, and the model reads them from there at runtime, so `Config` is the ground truth for those if a value here and a value there ever disagree. **The exception:** the momentum-regime cutoffs (the RSI thresholds that classify Accelerating Bullish/Bullish/Accelerating Bearish/Bearish) and the Improving/Deteriorating constants (the ~5-trading-day lookback, the RSI ±3 and ROC ±0.02 thresholds) are currently **hardcoded directly in `scoring.py`**, not read from Config — so for those specific values, `scoring.py` itself is the ground truth. See Section 6 for where this applies.*

---

## 1. Overview & Philosophy

This system is an automated, multi-factor technical-analysis dashboard. It scores and ranks a universe of stocks and ETFs across three dimensions of market behavior — **Trend, Momentum, and Volatility** — and tracks how those scores and states evolve over time.

The guiding idea is that technical analysis becomes far more useful when it is applied as a **structured, repeatable process** across a whole universe, rather than eyeballed one chart at a time. When every security is measured on the same consistent methodology, you can:

- Compare securities against each other on a common basis.
- Rank them objectively.
- Group them into meaningful buckets.
- And — most importantly — detect *change over time*, not just current state.

The model is built in three conceptual layers, and keeping them distinct is central to how it works:

1. **Indicators (the raw facts).** Objective technical measurements pulled or derived from price data — moving averages, MACD, RSI, ATR, Bollinger Bands, and so on.
2. **Regimes (the interpretation).** Plain-language labels that describe the *current state* of each dimension — e.g. "Strong Uptrend," "Deteriorating," "Orderly." This is the layer that translates numbers into meaning.
3. **Scores (the ranking).** Numeric values that convert those conditions into comparable rankings, culminating in a single combined **TechScore**.

This ordering matters: the indicators state the facts, the regimes explain the current condition in human terms, and the scores rank the security relative to others. Together they give both an *interpretation* system and a *ranking* system — you can ask both "what state is this in?" and "how does it stack up?"

A final principle: **the model is meant to be transparent and editable.** Every calculation is traceable, and every threshold and parameter lives in one central `Config` table so the methodology can be tuned over time without rewriting the underlying logic.

---

## 2. Why Three Dimensions

The model is built around Trend, Momentum, and Volatility because together they capture three different but complementary aspects of market behavior. No single indicator or timeframe gives a complete picture; these three cover **direction, force, and environment**.

- **Trend** describes *where* price sits relative to its important medium- and longer-term reference levels. It is the structural backbone — the answer to "is this thing broadly rising or falling, and how decisively?"
- **Momentum** describes whether directional *force* is strengthening, steady, or fading. It confirms or questions the trend — a rising trend with fading momentum is a different story than one with accelerating momentum.
- **Volatility** describes the *environment* — whether trading is compressed, orderly, active, or unstable. The same trend structure means something different in a calm environment than in a chaotic one, so volatility provides essential context.

Direction, force, and environment. A model built on all three is far more robust than one built on a single signal.

---

## 3. The Indicators

For each indicator below: what it measures, how to read it, why it's a useful signal, and why its specific duration/parameter was chosen. *(Confirm exact parameter values against the `Config` table.)*

### Trend indicators

**Close vs. 60-day moving average (MA60)** and **Close vs. 120-day moving average (MA120).**

- *What they measure:* where the current price sits relative to its own recent average. A moving average smooths out daily noise to reveal the underlying trend level. Comparing price to that average (as a ratio, e.g. Close/MA60) tells you how far above or below trend the security is trading.
- *How to read it:* a ratio above 1.0 means price is above that average (bullish structure); below 1.0 means below it (bearish structure). The further from 1.0, the more decisive.
- *Why it's a good signal:* price relative to a moving average is one of the most durable, widely-used trend measures — it captures structural direction without overreacting to single-day moves.
- *Why these durations:* the model uses **two horizons** deliberately. The **60-day** average is the medium-term reference — responsive enough to reflect meaningful moves without being noisy. The **120-day** average is the longer intermediate reference — a broader structural baseline. Using both lets the model distinguish a short-term wobble (60-day moves, 120-day holds) from a genuine larger trend shift (both move together). This two-horizon design is what lets it identify transitional states like early recoveries and early rollovers.

**Moving-average alignment (MAAlign).**

- *What it measures:* whether the 60-day and 120-day averages are close together / pointing the same way (within a small tolerance), i.e. whether the two trend horizons *agree*.
- *Why it's a good signal:* when both averages are aligned, the trend structure is coherent and more trustworthy; when they diverge, the trend is less structurally supported. This is why a security can have price above both averages yet still not qualify as a *strong* uptrend — if the averages themselves are diverging, the structure isn't fully confirmed.

### Momentum indicators

**MACD (12, 26, 9).**

- *What it measures:* the relationship between a faster (12-period) and slower (26-period) exponential moving average of price, plus a 9-period signal line. It captures the *acceleration* of a trend — whether directional force is building or fading.
- *How to read it:* MACD above its signal line and above zero indicates positive, strengthening momentum; below signal and below zero indicates negative momentum. The gap between MACD and its signal (the histogram) shows whether momentum is expanding or contracting.
- *Why it's a good signal:* MACD is a standard, well-understood measure of momentum shifts and is particularly good at flagging turns early, before they show up in slower trend measures.
- *Why these settings:* 12/26/9 is the classic, widely-validated MACD configuration — a sensible balance of responsiveness and reliability.

**RSI (14).**

- *What it measures:* the Relative Strength Index — the magnitude of recent gains versus recent losses, on a 0–100 scale. It measures the *strength* of momentum and identifies overbought/oversold conditions.
- *How to read it:* readings above ~50 lean bullish, below ~50 bearish; very high (e.g. >70) suggests overbought, very low (e.g. <30) oversold.
- *Why it's a good signal:* RSI is a robust, bounded momentum gauge that's especially useful for spotting exhaustion and recovery.
- *Why 14:* the 14-period setting is the standard — balanced, neither too twitchy nor too sluggish.

**ROC (20) — Rate of Change.**

- *What it measures:* the percentage change in price over the last 20 periods (roughly one trading month). A direct, simple read on recent momentum direction and magnitude.
- *How to read it:* positive ROC means price is higher than a month ago (upward momentum); negative means lower.
- *Why it's a good signal:* it's a clean, intuitive momentum measure that complements MACD and RSI by directly quantifying recent price change.
- *Why 20:* 20 periods ≈ one month of trading, a practical horizon for a swing-oriented model.

### Volatility indicators

**ATR ratio — ATR(20) vs. ATR(90) baseline.**

- *What it measures:* ATR (Average True Range) measures the typical size of daily price moves — i.e. how much the security moves in a day. The model computes recent volatility (ATR over 20 periods) and compares it to a longer 90-day baseline average of ATR, producing a **ratio**.
- *How to read it:* a ratio near 1.0 means current volatility is normal for this security; well above 1.0 means it's unusually volatile *for itself*; well below means unusually calm.
- *Why it's a good signal — and why a ratio:* raw volatility isn't very comparable across securities (a $500 stock and a $30 stock have very different absolute ranges). By measuring current volatility *against the security's own recent baseline*, the ratio makes volatility meaningful and comparable — it answers "is this security more or less volatile than it normally is?"
- *Why these durations:* ATR(20) captures recent (roughly monthly) volatility; the 90-day baseline gives a stable longer-run norm to judge it against.

**Bollinger Band Width (BBW) and its 125-period percentile.**

- *What it measures:* Bollinger Bands (20-period) are bands placed above and below a moving average based on standard deviation. Their *width* (BBW) expands when volatility rises and contracts when it falls. The model then computes where the current BBW ranks as a **percentile within the last 125 periods**.
- *How to read it:* a low percentile means bands are unusually *narrow* right now (a "squeeze" — compressed volatility, often a coiled-spring setup); a high percentile means unusually *wide* (elevated volatility).
- *Why it's a good signal — and why a percentile:* like the ATR ratio, the percentile puts current band width in the context of the security's own recent history, rather than an arbitrary absolute number. It answers "is this security unusually compressed or unusually expanded relative to its recent norm?"
- *Why 125 periods:* roughly six months of trading — a broad enough window to give the percentile real context.

---

## 4. Derived Variables

These are computed by the script from the raw indicators. *(Confirm exact formulas against `scoring.py`.)*

- **Price60** = Close / MA60 — price relative to the medium-term average.
- **Price120** = Close / MA120 — price relative to the longer-term average.
- **MAAlign** = a flag (1 or 0): 1 if the 60- and 120-day averages are within the alignment tolerance of each other, else 0.
- **ATR_Ratio** = ATR(20) / ATR(90) baseline — recent volatility vs. its own norm.
- **BBW** = (upper band − lower band) / middle band — the normalized width of the Bollinger Bands.
- **BBW_Percentile** = the percentile rank of the current BBW within the trailing 125 periods.
- **ROC20** = (Close / Close 20 periods ago) − 1 — the 20-period rate of change.

*All calculations use split- and dividend-adjusted prices, so corporate actions (like stock splits) don't create false signals.*

---

## 5. The Scoring System

The model turns the three dimensions into numbers, then sums them into a single score. *(Confirm exact threshold values against `Config` and the exact logic against `scoring.py`.)*

### Trend score

Trend is scored from three components:

- **Trend60Score** — based on Price60, in bands: strongly above the average earns the top score, mildly above earns a smaller positive, mildly below earns a small negative, and well below earns the bottom score. (The intended bands: strong ≈ Price60 ≥ 1.05, mild positive ≥ 1.00, mild negative ≥ 0.95, else the low score.)
- **Trend120Score** — the same idea against Price120, with wider bands appropriate to the longer horizon (strong ≈ ≥ 1.10, then ≥ 1.00, then ≥ 0.90, else low).
- **MAAlignScore** — a bonus point when the two averages are aligned.

**TrendScore = Trend60Score + Trend120Score + MAAlignScore.**

*Why the thresholds are where they are:* the bands are set so that being *decisively* above trend (e.g. 5%+ above the 60-day, 10%+ above the 120-day) is meaningfully rewarded over merely being above it, reflecting that a security trading well clear of its averages has stronger structure than one hovering right at them. The wider bands on the 120-day acknowledge that the longer average moves more slowly, so a larger deviation is needed to be meaningful.

### Momentum score

Momentum is first classified into a **regime** (see Section 6), then mapped to a numeric adjustment:

- Accelerating Bullish → **+3**
- Bullish → **+2**
- Improving → **+1**
- Neutral → **0**
- Deteriorating → **−1**
- Bearish → **−2**
- Accelerating Bearish → **−3**

This is **MomentumAdj**.

### Volatility score

Volatility is also classified into a regime, then mapped:

- Compressed / Squeeze → **+2**
- Orderly → **+1**
- Expanding → **0**
- Elevated → **−1**
- Extreme / Unstable → **−2**

This is **VolAdj**.

*Why compression scores positively:* this is a deliberate design choice. In this model, low-and-orderly volatility is treated as the *constructive* backdrop — a compressed "squeeze" often precedes a directional move and represents a low-risk setup, so it earns a positive adjustment. High, unstable volatility is treated as risk and penalized. The color-coding on the dashboard matches this: compression reads green, instability reads red, consistent with how the score treats them.

### Final score

**TechScore = TrendScore + MomentumAdj + VolAdj.**

This is the headline number used to rank securities. It combines structural direction (Trend, the largest component), directional force (Momentum), and environment/risk (Volatility) into one comparable figure. A high positive TechScore means a security that is structurally strong, with confirming momentum, in a constructive volatility environment; a deeply negative one means the opposite on all three.

---

## 6. Regime Classification

Regimes translate the numbers into plain-language *state* labels. They are the interpretation layer — what makes the dashboard readable at a glance.

**A key rule: regimes are evaluated top-down, first-match-wins.** The conditions are checked in order from the most extreme/specific to the least, and the first one that matches is assigned. This ordering matters because conditions can overlap — evaluating most-extreme-first ensures, for example, that a security meeting *all* the criteria for "Strong Uptrend" is labeled that, rather than falling into the more general "Uptrend" that it also technically satisfies. *(Confirm exact conditions against `scoring.py`.)*

### Trend regimes

- **Strong Uptrend** — price decisively above both averages *and* the averages are aligned. The most constructive structure.
- **Uptrend** — price above both averages, but not with the strongest structure (e.g. alignment not met).
- **Early Uptrend / Recovery** — the shorter-term trend has turned up while the longer-term hasn't fully recovered. An emerging positive.
- **Neutral / Transition** — price near key reference levels with no decisive condition; no clear edge.
- **Early Downtrend / Roll Over** — shorter-term weakness appearing before a full longer-term breakdown. An early warning.
- **Downtrend** — price below both averages; established weakness.
- **Strong Downtrend** — price decisively below both averages; broad technical weakness.

### Momentum regimes

- **Accelerating Bullish** — momentum positive and broadly strengthening (MACD above signal and above zero, strong RSI, positive ROC).
- **Bullish** — momentum positive but not at the strongest confirming level.
- **Improving** — see the special section below; momentum turning up before it earns full Bullish status.
- **Neutral** — no clear directional momentum edge.
- **Deteriorating** — see below; momentum rolling over before it becomes fully Bearish.
- **Bearish** — momentum negative, confirming weakness.
- **Accelerating Bearish** — momentum negative and broadly worsening.

### Volatility regimes

- **Compressed / Squeeze** — volatility unusually compressed (low ATR ratio *and* low BBW percentile); often a setup phase.
- **Orderly** — normal, controlled volatility.
- **Expanding** — volatility increasing / the chart becoming more active.
- **Elevated** — volatility materially above normal.
- **Extreme / Unstable** — volatility highly elevated (high ATR ratio *and* high BBW percentile); an unstable environment.

### The Improving / Deteriorating refinement (momentum)

These two momentum regimes are special because, unlike every other regime in the model, they require comparing the *present against the past* — they detect *change* in momentum, not just its current level. They exist as **"catching the turn" early-warning states**: *Improving* catches momentum waking up **before** it earns a full Bullish label, and *Deteriorating* catches it fading **before** it collapses to Bearish. They sit between the steady-state regimes in the top-down order.

The logic compares today's momentum inputs against the values from roughly **5 trading days earlier** (the nearest prior snapshot ~5 trading days back). To be labeled *Improving*, a security must not already be Bullish/Accelerating Bullish, **and all three** of these must be turning up versus ~5 days ago:

*Unlike the trend and volatility thresholds elsewhere in this document, the lookback and the three thresholds below are **not** Config parameters — they're hardcoded constants inside `scoring.py`. `scoring.py` is the ground truth for these specific values; changing them requires editing and redeploying the script, not editing a Config row.*

- The MACD histogram (MACD minus its signal) has increased.
- RSI has risen by at least **3 points**.
- ROC has risen and moved toward/above zero (by at least **0.02**, i.e. two percentage points, in fractional terms).

*Deteriorating* is the exact mirror (not already Bearish, and all three turning down).

**Why "all three must agree":** this rule was a deliberate tightening. An earlier version required only *two of three* conditions, which caused these regimes to fire on roughly a third of all days and to flip back and forth in choppy periods — that's noise, not signal. Requiring **all three inputs to agree** raises the bar to a genuinely broad-based momentum turn. In testing during development, this tightened version fired on roughly **17%** of days, in spaced-out streaks with neutral gaps between them — meaningful transitions rather than chatter. That 17% figure is a development-time observation from testing, not a continuously-verified live metric — it isn't recomputed or asserted anywhere in the code, so treat it as directional rather than exact if you're checking current behavior. The specific thresholds (RSI ±3, ROC ±0.02) are modest-but-non-trivial: large enough to filter daily wiggle, small enough to catch a real turn early. If a security has no snapshot ~5 days prior (e.g. at the very start of its history), these regimes are skipped and it falls through to Neutral.

*These thresholds are tunable, but not from Config.* Because they're hardcoded in `scoring.py` (see above) rather than read from the Config table, tuning them means editing that file directly, then committing and deploying the change — unlike the trend/volatility thresholds, which take effect on the next run just by editing a Config row. The model is transparent and history is stored, so you can still observe how often Improving/Deteriorating actually fire and adjust the lookback or thresholds in code if they feel too twitchy or too quiet.

---

## 7. Historical Tracking & Checkpoints

A current score tells you what a security looks like *now*. The historical checkpoints tell you whether it's **improving, deteriorating, or stable** — which is often more valuable than the level itself. This is what makes the system a *change-detection* tool, not just a static ranking.

The model tracks:

- **TechScore** at six checkpoints: 1 week, 2 weeks, 3 weeks, 1 month, 2 months, and 3 months ago.
- **Each regime** (Trend, Momentum, Volatility) at four checkpoints: 1 week, 2 weeks, 3 weeks, and 1 month ago.

*(The score is tracked out further — to 3 months — because a single number's longer trajectory is informative; regimes are tracked only out to 1 month because how a regime looked recently is what's actionable, and carrying every regime out to 3 months would add many columns for little added insight.)*

**The retrieval rule:** for each checkpoint, the model retrieves the **most recent snapshot on or before the target date** — the nearest prior available snapshot. This is necessary because trading calendars don't have data on every exact target date (weekends, holidays), so requiring an exact-date match would leave gaps. Taking the nearest prior snapshot gives a consistent, always-available value.

**Where the checkpoint values actually come from:** every run pulls ~2 years of EODHD history per symbol and scores every date in that window in memory (see `DEPLOYMENT.md` §5) — the checkpoints above are retrieved by looking backward within that same in-memory window, not from any separately persisted table. So the checkpoints are recomputed fresh every run rather than read back from stored history; that's what lets them be fully populated from a security's very first run rather than only after months of accumulated snapshots.

Separately, the model also keeps a permanent audit log of every day's computed scores and regimes — for ad-hoc historical analysis later, not for anything the live model reads back. That log is a plain `history_log.jsonl` file in the repo, one line per symbol per day: the very first run bootstraps it with every scoreable date across the full ~2-year pull (since that's already computed in memory anyway, at no extra cost), and every run after that just appends the latest day. This log previously lived in an Airtable table (`Scores_History`); it was moved to a repo file to keep the Airtable base's record count small enough for the free tier — see `DEPLOYMENT.md` for the full reasoning.

---

## 8. How to Read the Dashboard

Each row tells a story left to right:

1. **Symbol and Name** — the security.
2. **Current TechScore** — the headline strength, color-coded green (strong) through yellow (neutral) to red (weak).
3. **Score history (1W → 3M)** — the same score at each past checkpoint, color-coded. Comparing the current score to these earlier values shows the *trajectory* — whether the security has been strengthening or weakening over recent weeks and months.
4. **Current regimes** — the trend, momentum, and volatility states right now, color-coded.
5. **Regime history (1W → 1M)** — how each regime has evolved recently.

**Color convention throughout:** green = constructive, yellow = neutral/transitional, red = concerning. This holds for both the numeric scores and the regime labels, so the whole dashboard can be scanned by color alone.

**Interpreting combined states** — a few illustrative examples:

- *Strong Uptrend + Bullish + Orderly* → a strong, clean, constructive setup: structure, force, and environment all favorable.
- *Strong Uptrend + Deteriorating + Elevated* → still in an uptrend, but momentum is fading and volatility is rising beneath the surface — an early caution flag even though the trend label is still positive. This is exactly the kind of divergence the multi-dimension design is meant to surface.
- *Early Uptrend / Recovery + Improving + Compressed/Squeeze* → an emerging setup: trend turning up, momentum beginning to confirm, volatility coiled — potential for expansion.
- *Downtrend + Bearish + Expanding* → weak structure with worsening participation.
- *Strong Downtrend + Accelerating Bearish + Extreme/Unstable* → a high-risk breakdown environment.

The most valuable reading often comes from **combining current state with the history** — e.g. a still-high TechScore that has been steadily declining across the checkpoints is a very different situation from an equally-high score that has been climbing, even though today's number is identical. Always read the trajectory, not just the point.

---

*End of methodology. For build, schema, hosting, and operations, see `DEPLOYMENT.md`.*
