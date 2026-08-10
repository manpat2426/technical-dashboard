# Technical Analysis Dashboard — Methodology

*This document explains the **what** and the **why** of the technical model: what each indicator measures, why it was chosen, how the scores and regimes are built, and how to read the output. It is the companion to `DEPLOYMENT.md`, which covers how the system is built and hosted.*

*A note on precise values: this document explains the reasoning and structure of the model. Most parameters — the trend thresholds, the volatility thresholds, and every indicator's period/lookback — live in the **`Config` table** in the Airtable base, and the model reads them from there at runtime, so `Config` is the ground truth for those if a value here and a value there ever disagree. **Two exceptions:** (1) the momentum-regime cutoffs (the RSI thresholds that classify Accelerating Bullish/Bullish/Accelerating Bearish/Bearish) and the Improving/Deteriorating constants (the ~5-trading-day lookback, the RSI ±3 and ROC ±0.02 thresholds) are hardcoded in `scoring.py` (see Section 6); and (2) the decision layer's parameters — the smoothing window, band cutoffs, boundary buffer, per-band slope thresholds, and persistence requirement — are hardcoded in a labeled constants block in `decision.py` (see Section 8). For those specific values, the respective script file is the ground truth, not Config.*

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

## 8. The Decision Layer

Everything up to this point produces **TechScore** — a single number describing a security's technical state on a given day. The decision layer sits on top of that number and answers a different question. TechScore tells you *what condition a security is in*; the decision layer tells you *whether that condition has changed enough, and durably enough, to act on*. Those are not the same question, and conflating them is what makes a raw score frustrating to use for allocation.

### 8.1 Why a separate layer exists

TechScore is a faithful daily instrument, and that faithfulness is exactly the problem when you try to make slower decisions off it. Two structural facts make the raw score bounce day-to-day:

- **It is built from hard threshold cutoffs.** Every component score snaps between integer levels at fixed boundaries — for example, Trend60Score jumps from +1 to +3 the instant Price60 crosses 1.05. A security oscillating around 1.049–1.051 flips a two-point swing in TechScore on noise alone, with nothing meaningful changing about the company. Compounded across the trend bands and the two regime mappings, the score can move several points between consecutive days without any real change underneath.
- **It is memoryless.** Each day is scored fresh from that day's indicators. Nothing in TechScore knows what yesterday's score was — there is no smoothing, no persistence, no notion of a trend *in the score itself*.

The result is a number that is genuinely informative but too twitchy to allocate on directly. If you acted on every TechScore move you would be round-tripping positions weekly on noise — churning in and out of the same securities as they wobble across a threshold and back. The point of a portfolio tool for a monthly-ish cadence is the opposite: to hold through the chop and act only when something has *really* changed.

The decision layer's job, therefore, is to convert the noisy daily score into a small set of **stable, actionable states** — to tell you when a security is *beginning to improve*, when it is *steady* (whether constructively or weakly), when it is *starting to concern*, and when it is decisively *time to exit*. It deliberately trades away daily resolution (which you don't want to act on) in exchange for a signal you can hold to for weeks.

### 8.2 The six states

The layer classifies each security, each day, into exactly one of six states:

- **Improving** — not yet strong, but durably turning up. *Candidate to add / initiate.*
- **Steady-constructive** — strong and holding. *Core hold.*
- **Neutral / No Signal** — middling and going nowhere; no clear edge. *Watch.*
- **Concerning** — was strong or decent, now durably rolling over. *Trim / tighten.*
- **Steady-weak** — weak and stable; bad and staying bad. *Stay out.*
- **Exit** — already weak and actively breaking down further. *Get out.*

The actions in italics are how the states are *intended* to be used; the layer itself only emits the label. Mapping states to concrete position sizes or trim amounts was deliberately left out of this version — the states are informational, so that a feel for how they behave can be built before any mechanical action rule is committed to.

### 8.3 The two ingredients: level and slope

Every decision is computed from just two properties of a **smoothed** version of the score, and understanding why *both* are needed is the core of the design.

First, the smoothing. The layer never reads raw TechScore. It works off **`sTech`**, a trailing 15-trading-day (roughly three-week) rolling mean of TechScore. This single step removes most of the single-day threshold-flip noise described above: a one-day cliff-crossing barely moves a 15-day average. Everything below — every level and every slope — is measured on `sTech`, not on the raw score.

From `sTech` come the two ingredients:

- **Level** — *how good is it right now?* The value of `sTech` itself. A high level means structurally strong, with confirming momentum and constructive volatility, today.
- **Slope** — *which way is it going?* The change in `sTech` across recent checkpoints — climbing, flat, or falling over recent weeks.

Neither alone is sufficient, and the reason maps directly onto the six states. A **level-only** classifier can rank securities by current strength but is blind to change: it cannot express "was strong, now fading" (it just sees "still fairly strong" and says hold, until the level finally collapses — by which point you are late). A **slope-only** classifier can detect turns but has no anchor: a security rising from terrible to slightly-less-terrible looks identical to one rising from good to great, even though one is "stay out, still weak" and the other is "core holding strengthening."

So the six states live in a **two-dimensional grid** — level on one axis, slope on the other — and each state is a level-and-slope pair. The same slope means opposite things at different levels: rising from a low level is **Improving**; falling from a high level is **Concerning**; falling from an already-low level is **Exit**; flat at a high level is **Steady-constructive**; flat at a low level is **Steady-weak**; flat in the middle is **Neutral**. This is why the layer needs both ingredients — the states are the corners and edges of a grid, not points on a single line.

### 8.4 Level-dependent slope sensitivity — the key design choice

The most consequential decision in the layer is how level and slope *arbitrate when they disagree* — specifically, what to do with a security that is still strong in level but has clearly begun rolling over in slope. The answer here is deliberately **not** a fixed rule. Instead, **how much authority a falling slope has depends on how strong the level still is.**

The intuition is how you would actually think about a holding. Picture the same "clearly fading" slope on two securities. One sits at a *very high* level — a strong holding giving back a little from a peak; that fade is often just consolidation after a run, and you want to *tolerate* it rather than trim on it. The other sits at a *middling* level — already only moderately strong, and now fading too; that same slope is a genuine "was okay, now deteriorating," and you want it to *count*. Same fade, opposite meaning, and the level is what decides which.

This is implemented as three **level bands**, where the band sets both how much a fade is tolerated and how the slope is measured:

- **High band** — strong level. A fade must be *large and sustained* before it overrides to Concerning; small and moderate fades are absorbed, and the security stays Steady-constructive. Level dominates here — strength gets the benefit of the doubt.
- **Middle band** — moderate level. Slope and level share authority; a *clear* fade is enough to move the security to Concerning, and a clear rise is enough for Improving. This is where slope earns real say.
- **Low band** — weak level. The security is near the floor already; a still-falling slope confirms Exit, a flat slope is Steady-weak, and a clear rise is the early Improving turn.

The deliberate consequence — worth stating plainly — is that this design **exits its strongest holdings later than a slope-wins rule would.** By giving high-level securities room to fade before acting, the layer will occasionally tolerate a fade that turns out to be a real reversal. That is an accepted trade, not an oversight: for a tool whose entire purpose is to avoid being shaken out of good positions on noise, patience at the top is the correct bias. The cost is being a little late on the occasional genuine top; the benefit is not churning out of strong holdings on every wobble.

### 8.5 Slope spacing: fast where it matters, slow where it doesn't

*Over what horizon* the slope is measured is the knob that sets how fast the layer reacts, and it too is level-dependent — for a specific reason.

- In the **High band**, slope is read on **month-scale spacing**: `sTech` now versus roughly one month ago, confirmed against roughly two months ago. Wide spacing is inherently patient — a month is long enough that noise averages out and only a real trajectory shift registers. This matches the "hold through consolidation" intent of the high band: confirming a strong security is *still* strong does not need weekly resolution.
- In the **Middle and Low bands**, slope is read **week-over-week**: `sTech` now versus 1, 2, and 3 weeks ago. Here the faster read is the point — the "Concerning" and "Exit" transitions are the time-sensitive ones, where catching the roll early is what protects capital. The jumpiness of a fast read is defended against not by widening the spacing (which would make the layer structurally late on exactly the states where lateness costs most) but by the persistence and confirmation rules below.

In both cases the slope only counts if it is **directionally consistent** — the steps across the checkpoints must agree (all falling, or all rising). A mixed or unconfirmable sequence reads as flat, so a single erratic checkpoint can never move the decision. And a checkpoint with no data available (too early in a symbol's history) also reads as flat rather than guessing — an unconfirmable slope is never allowed to drive a state change.

### 8.6 Two kinds of hysteresis — why the states don't flip

A classifier built on thresholds would reintroduce exactly the bouncing it was meant to remove — now at the level of the decision instead of the score. Two separate stabilizers prevent that.

**Band-boundary hysteresis.** Because the slope *spacing itself* changes at the high/middle band boundary (month-scale above, week-over-week below), a security drifting across that line would otherwise switch how its trajectory is measured at the worst possible moment, producing a jump. To prevent this, the band boundaries have a buffer: you *enter* a band by crossing its cutoff, but you don't *leave* it until `sTech` clears the cutoff by a further margin. A security hovering near a boundary therefore stays in the band it was already in rather than oscillating across the seam.

**Decision-transition persistence.** A newly-computed state does not become the *official* decision until it has held for two consecutive days. A single day that trips a threshold and reverses the next day never becomes the shown decision. (The one exception is bootstrap: the very first date that produces any state adopts it immediately, since there is no prior decision to carry forward.)

Together, the 15-day smoothing, the directional-consistency requirement, the band-boundary buffer, and the two-day persistence are four independent layers of noise suppression — which is what lets the output be something you can hold to for weeks rather than a label that flickers.

### 8.7 How the decision is computed and stored

Because the decision depends on smoothing, on which band the security was *previously* in, and on how many days a pending state has held, it cannot be computed one date at a time in isolation — it is inherently **stateful**, depending on the days that came before. So for each symbol it is computed as a **forward pass over the scored history, oldest date to newest**, carrying the current band, the current official decision, and the pending-state counter as it goes.

A crucial property follows from this: the decision written for each historical date is the **honest point-in-time value** — what the tool *would have said on that date*, using only data available up to it. Today's decision is never applied backward over history. This is what makes the stored decision history a faithful record you could genuinely backtest against, rather than a smoothed-in-hindsight rewrite.

The whole series is **recomputed from scratch every run**. This is possible — and cheap — because the pipeline already recomputes roughly two years of scored history in memory on every run for the checkpoint calculations (see §7); the decision forward pass simply rides on that same in-memory series. Nothing about the decision needs to be persisted and read back to function, so a newly-added security gets a fully-populated decision history (and populated checkpoints) on its very first run, exactly like the scores do. The decision is also appended to the permanent `history_log.jsonl` audit file alongside each day's score, purely so the state history is available for ad-hoc analysis later — not because the live pipeline ever reads it back.

The decision is tracked at checkpoints the same way regimes are — current plus 1 week, 2 weeks, 3 weeks, and 1 month ago — using the identical nearest-prior-date retrieval rule (§7). It is tracked out to one month rather than three because, like a regime, the decision is an already-slow, already-smoothed object whose *recent* evolution is what's actionable; carrying it to three months would add columns for little added insight.

### 8.8 Calibration philosophy, and a known tuning candidate

The band cutoffs and slope thresholds are **construction-based**, not fitted to the observed distribution of scores. That is a deliberate choice tied to how the tool is used: the security universe is fluid — securities are added and removed over time — so a distribution-relative cutoff ("the top quartile of *today's* holdings") would quietly shift meaning every time the basket changed. A construction-based cutoff is an *absolute* yardstick: a given `sTech` level means the same structural thing regardless of which, or how many, securities are currently held. For a fluid universe, absolute is the correct basis.

All of the layer's tunable numbers — the smoothing window, the band cutoffs, the boundary buffer, the per-band slope thresholds, and the persistence requirement — live together in one labeled constants block at the top of `decision.py` (see `DEPLOYMENT.md` §5). They are not Config-table parameters; tuning them means editing that file and redeploying. They were grouped deliberately in one place so that tuning is a single obvious edit rather than a hunt through the logic.

One tuning candidate is worth recording explicitly, because it is a known and intended property rather than a defect. The Middle band's slope bar is demanding: on a 15-day mean, requiring a two-point move with all weekly steps agreeing means Middle-band **Improving** and **Concerning** fire relatively rarely, and most middle-band securities read **Neutral / No Signal**. This is by design — Neutral is meant to be an honest residual ("nothing here warrants action"), not a signal in disguise, and forcing more securities out of it would manufacture false precision. But if live observation ever shows the layer sitting silently in Neutral through moves you would genuinely have wanted flagged, the single highest-leverage adjustment is lowering the middle-band slope threshold; a softer alternative is relaxing the requirement that *all* the weekly steps agree to just the two most recent. Either is a one-line change in the constants block — with the understood cost that loosening the bar raises the state-change rate, trading some of the stability the layer was built to provide. The right time to make that change is after watching real behavior accumulate, not up front.

---

## 9. How to Read the Dashboard

Each row tells a story left to right:

1. **Symbol and Name** — the security. These two columns stay pinned in place while the rest of the table scrolls sideways.
2. **Current decision** — the decision-layer state (§8): the tool's actionable read of the security. It leads the table, immediately after Symbol/Name, because it is the conclusion everything else supports — you read it first and only look rightward if you want to know *why*. Color-coded on the same green-to-red convention.
3. **Decision history (1W → 1M)** — how the decision has evolved recently, so a state change is visible as a trajectory ("Neutral → Improving → Steady-constructive") rather than a bare current label.
4. **Current TechScore** — the headline strength, color-coded green (strong) through yellow (neutral) to red (weak).
5. **Score history (1W → 3M)** — the same score at each past checkpoint, color-coded. Comparing the current score to these earlier values shows the *trajectory* — whether the security has been strengthening or weakening over recent weeks and months.
6. **Current regimes** — the trend, momentum, and volatility states right now, color-coded.
7. **Regime history (1W → 1M)** — how each regime has evolved recently.

The ordering is deliberate: **conclusion first, evidence after.** The decision block answers "what should I do?", the TechScore block answers "how strong is it, and has that been changing?", and the regime blocks answer "which of trend, momentum, or volatility is driving that?" Each step rightward is one level deeper into the reasoning.

**Color convention throughout:** green = constructive, yellow = neutral/transitional, red = concerning. This holds for the numeric scores, the regime labels, and the six decision states alike, so the whole dashboard can be scanned by color alone. For the decision states specifically: Steady-constructive is the deepest green and Improving a step lighter (both constructive, but distinguishable at a glance — "hold" versus "add"), Neutral / No Signal reads yellow, and Concerning, Steady-weak, and Exit step through orange and red to the deepest red.

**Interpreting combined states** — a few illustrative examples:

- *Strong Uptrend + Bullish + Orderly* → a strong, clean, constructive setup: structure, force, and environment all favorable.
- *Strong Uptrend + Deteriorating + Elevated* → still in an uptrend, but momentum is fading and volatility is rising beneath the surface — an early caution flag even though the trend label is still positive. This is exactly the kind of divergence the multi-dimension design is meant to surface.
- *Early Uptrend / Recovery + Improving + Compressed/Squeeze* → an emerging setup: trend turning up, momentum beginning to confirm, volatility coiled — potential for expansion.
- *Downtrend + Bearish + Expanding* → weak structure with worsening participation.
- *Strong Downtrend + Accelerating Bearish + Extreme/Unstable* → a high-risk breakdown environment.

The most valuable reading often comes from **combining current state with the history** — e.g. a still-high TechScore that has been steadily declining across the checkpoints is a very different situation from an equally-high score that has been climbing, even though today's number is identical. Always read the trajectory, not just the point.

The decision column is the shortcut for exactly this reading: it already folds level and trajectory together into a single actionable label, so a security whose TechScore is still high but whose decision has moved to **Concerning** is telling you — in one word — precisely the "high but declining" situation the raw numbers would make you infer. Read the decision *history* the same way you read the score history: a decision that has held **Steady-constructive** for a month is a settled core holding, whereas one that just flipped into **Improving** or **Concerning** is a fresh change worth attention.

---

*End of methodology. For build, schema, hosting, and operations, see `DEPLOYMENT.md`.*
