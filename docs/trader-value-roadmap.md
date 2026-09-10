# Making the engine more useful to a trader

Ordered by value per unit of work, and written against what is already in this repo. Everything here stays inside
the project rule: **the engine explains economic impact, it never emits entries, stops, sizing or signals.** The
gold / forex / crypto engines are the consumers that turn this into positions.

Two of these are already delivered (marked ✅) — they are listed so the sequence reads as one plan.

## 0. What was just shipped ✅

- **Per-asset outlook in the delivered text** (`app/outlook.py`). Every brief and alert now names the direction in
  words, the path across the three horizons, the mechanism in the analyst's own sentence, and the weight of the
  evidence. A bare `XAUUSD -38` is not information a reader can act on; "gold expected lower, strongest now and
  fading, because real yields rise and gold pays no coupon" is.
- **The engine's own hit rate travels with the read** (`outlook.track_record`). `market_reactions` already measures
  what the market actually did at 1h/4h/24h against the expected direction; the brief now publishes that as
  "7 of 11 confirmed", and withholds a rate below 5 directional checks. A call is worth what its history says.

## 1. Surprise, not level — a rolling surprise index per currency

**Why:** markets price the *gap* between the print and the consensus, not the print. That is the whole premise of
the Citi Economic Surprise Index, and it is the first thing a macro desk looks at.

**What exists:** `raw_articles.extraction` already carries `actual`, `forecast`, `previous`, `unit` and `surprise`
for every data print, and `economic_releases` carries the calendar consensus. Nothing aggregates them.

**Build:** a `surprise_index` table — per currency (or region), a decayed rolling sum of standardised surprises over
~3 months, journalled like `macro_state`. Deliver one line per currency in the brief: "US data has beaten consensus
in 7 of the last 11 prints; the surprise index is +0.6 and rising."

**Payoff:** it tells the reader whether the economy is running hotter or colder *than what is priced*, which is the
question the macro state does not answer.

## 2. Pre-release scenario map — the "if / then" a desk writes before the print

**Why:** the highest-value macro work happens *before* the release. Professionals map each outcome to an expected
reaction in advance, precisely so they are not reasoning while the tape moves.

**What exists:** `intel.upcoming_releases` already returns the next 48h with `forecast` and `previous`, and
`asset_impacts` holds every past per-asset score keyed to `event_type`.

**Build:** for each HIGH-impact release in the window, one analyst call producing three branches — above / in line /
below consensus — each with per-asset expected direction and magnitude, seeded with the historical impacts of the
same `event_type` under a comparable macro state. Store as a `release_scenarios` row; deliver in the brief under the
release; re-use as the prior when the actual print arrives (which also makes the post-release analysis faster).

**Payoff:** this is the single feature most likely to change what a reader does, because it arrives before the move
rather than after it. It is still analysis, not a signal: branches and expected impact, never an entry.

## 3. Reaction shape — does the knee-jerk hold or snap back?

**Why:** the standing advice on news trading is not to trade the first print but to wait for the initial spike to
settle. Whether a move continues or reverts is a measurable property, and the engine is already collecting the data
to measure it.

**What exists:** `market_reactions` measures 5m / 15m / 1h / 4h / 24h with `change_pct` and an `interpretation`.

**Build:** classify the sequence per event × asset — `SPIKE_AND_REVERT`, `TRENDED`, `DELAYED`, `NO_REACTION` — and
aggregate by `event_type` and asset. Deliver as part of the outlook: "the last 6 CPI prints moved gold at 15m and
gave most of it back by 24h."

**Payoff:** answers the "will it keep going?" question that per-horizon scores only imply.

## 4. Historical analogs from the vector store

**Why:** structured comparison with similar past episodes is the strongest form of context, and current research on
event-driven market analysis is converging on exactly this (retrieve analogous past events, read what followed).

**What exists:** `knowledge_embeddings` already stores an event+analysis vector per analyzed event, and
`market_reactions` records what followed. The retrieval half is built and unused for this purpose.

**Build:** on each new analysis, retrieve the k nearest past events, filter to a comparable macro state, and attach
"what actually happened next" — the measured 4h/24h reactions of those analogs. Deliver one line per asset:
"the 3 closest past analogs moved gold +0.8% / -0.2% / +1.1% at 24h."

**Payoff:** grounds every read in outcomes instead of reasoning alone, and it is nearly free — the embeddings and the
reactions are both already stored.

## 5. Calibrate the published scores against the track record

**Why:** an uncalibrated ±60 from a channel that has been wrong 8 times out of 10 looks as authoritative as a good
one. §0 publishes the hit rate; the next step is to let it *scale* what is published.

**Build:** a per-asset, per-event-type, per-horizon multiplier derived from `market_reactions`, applied as a
published `calibratedScore` alongside the raw score — never overwriting it (the "never silently rewrite model
output" rule). Downstream engines choose which to read.

## 6. Close the data gaps that matter most to a trader

Ranked by what they add per unit of integration work. Each is a collector in `sources/registry.json` plus a table:

1. **Positioning** (CFTC Commitments of Traders, weekly, free) — tells you whether a surprise lands on a crowded
   position, which is what turns a small surprise into a large move.
2. **Options-implied expected move** for the event day (gold, BTC, SPX) — the market's own estimate of how big the
   reaction should be, which is the honest yardstick for the engine's magnitudes.
3. **ETF flows** (crypto and gold) — already an `event_type` (`ETF_FLOWS`) with no dedicated feed; it is the clearest
   observable demand channel for BTC and XAU.
4. **Rate-path pricing** (fed funds futures / OIS implied cut probabilities) — the engine reasons about the policy
   path constantly and currently has no measured version of it.
5. **Real-time cross-asset prices** beyond the reaction fetcher, so the outlook can say what has already moved.

## 7. The current bottleneck is the analyst model, not the pipeline

On this host, **25 of the last 29 analyses carry self-consistency flags**, and the live brief contains rationales that
argue the wrong instrument — a USD score explained by the yen and JGB yields. `GET /analysis-quality` attributes most
of those flags to `llama3.1:8b` (20 of 21 analyses flagged, mainly `zero_all_horizons` and `zero_with_direction`);
the handful written under `qwen2.5:7b` are unflagged so far, but two analyses is not a sample that ranks a model, so
run `scripts/compare_models.py` before concluding anything from it. Either way the decomposition in
`config.decompose_analysis` and the flags in `app/consistency.py` are containing the damage, not fixing it.

**Recommendation:** route by importance rather than choosing one provider — events at importance ≥ 85 (FOMC, CPI,
NFP, war escalation) to `claude-opus-5`, everything else to the local model. That is roughly a dozen events a day at
Anthropic prices, and it is the difference between a reader trusting the top of the brief or discounting all of it.
`app/config.model_for` is already the single place that decides the model per stage.

## 8. Delivery polish

- **Khmer prose needs `ANTHROPIC_API_KEY`.** With `OUTPUT_LANGUAGE=km` the labels, states and outlook vocabulary are
  Khmer from the static tables in `app/i18n.py`, but the analyst's own sentences fall back to English unless
  `TRANSLATE_MODEL` (Anthropic, by design — the local 8B models were not good enough at Khmer to publish) has a key.
- **Weekly brief** — the daily brief answers "what changed"; a weekly answers "what is the regime", which is the
  horizon most readers actually trade.
- **Ask-the-engine endpoint** — `intel.analysis_for_asset` already assembles everything needed to answer "why is
  gold bid this week?" on demand, with the drivers and the reaction scorecard attached.

## On market manipulation

Reading *how* price gets moved around news — thin liquidity into a print, crowded positioning, stop runs, who is
forced to trade — is legitimate market microstructure, and items 1, 3 and 6.1 above are how this engine would see
it. Building anything to *manipulate* a market (wash trading, spoofing, coordinated ramps, fake news injection) is
out of scope: it is illegal in every venue this engine covers, and none of it is in this repo's plan.
