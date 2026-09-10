# Cambotix Economic Intelligence Engine

A standalone economic brain for the Cambotix trading stack. It continuously answers *what is happening in the world
economy, why it matters, what changed, and which assets it can affect* — and exposes that as a structured API for the
gold, forex and crypto engines that come later. **It does not trade.**

```text
 Fed / ECB / BLS / BEA / SEC / CFTC       financial media       crypto media       ForexFactory calendar
        │ RSS                                  │ RSS / Alpha Vantage      │ RSS                 │ JSON
        └──────────────────── n8n collectors (every 5–60 min) ────────────┴─────────────────────┘
                                          │ POST /ingest/*
                                    ┌─────▼──────┐   normalize · hash · dedupe · keyword priors
                                    │   engine   │   stage 1  extractor  (claude-haiku-4-5)  → facts
                                    │  FastAPI   │   cluster: key → pgvector similarity → lexical fallback
                                    │            │   stage 2  analyst    (claude-opus-5)     → impact, causal chain
                                    └─────┬──────┘   macro-state updater · reaction tracker · daily brief
                                          │
                              PostgreSQL 17 + pgvector  (events, analyses, impacts, macro_state, memory)
                                          │
              GET /macro/current   /events/recent   /events/upcoming   /events/{id}   /analysis/{asset}   /briefs/latest
```

## Start locally

Requires Docker with Compose and Python 3.12+ on the host (no host packages are installed).

```bash
bash scripts/start.sh
```

This generates `.env` secrets, starts Postgres (pgvector), n8n and the engine, imports and publishes the twelve n8n
workflows, and prints a status view. Open **http://localhost:5681** and create the local n8n owner account — the
workflows are already active. Existing n8n installs on 5678/5679/5680 are unaffected.

Nothing else is required: the AI runs locally on **Llama 3.1 8B through Ollama**, pulled by `start.sh`. On macOS a
project-local, checksum-verified Ollama serves on `127.0.0.1:11436` with Metal acceleration (binaries, models and logs
under `.local/ollama`, separate from any system Ollama); elsewhere the `docker-ai` compose profile runs it in a
container, with `compose.gpu.yaml` for NVIDIA passthrough. Embeddings use `nomic-embed-text` locally, so pgvector
memory works with no API key at all.

Optional keys in `.env`:

```bash
ALPHA_VANTAGE_API_KEY=...           # hourly aggregated news (free tier ≈ 25 req/day)
COINGECKO_API_KEY=...               # higher rate limits for the reaction tracker
```

To switch the analyst to Claude instead (see the quality note below):

```bash
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
docker compose up -d engine          # EXTRACT_MODEL / ANALYST_MODEL fall back to the provider's defaults
```

`AI_PROVIDER=mock` is the deterministic offline stand-in used by tests and the smoke run.

### Local-model throughput and quality

> **One context size, always.** Ollama keys its loaded model instance on `num_ctx`, so varying it per call stage makes
> it unload and reload the whole 4.9 GB model between calls. That turned a 20-second scoring call into a 300-second
> timeout on 16 GB. `OLLAMA_NUM_CTX` (default 16384) is used by every call for this reason — don't make it per-stage.

Measured on an M1 Pro / 16 GB with `llama3.1:8b`: **~25 s per article** for extraction, **~20 s per scoring call**
and **~2.5 min per event** for a decomposed analysis. That comfortably outpaces arrivals (~15 articles/hour from the current feed set). Local inference is serial,
so `/process/extract` and `/process/analyze` hold a lock and answer `{"skipped": "busy"}` if a tick arrives while a
previous one is still running — the n8n executions stay green.

A single call asked to fill the whole nested `Analysis` schema is where a small model falls apart: llama3.1:8b
hedged every asset score to 0, reused one rationale for eight assets, and called an event risk-off while scoring
equities bullish. So for local models the analyst is **decomposed** (`ANALYST_DECOMPOSE`, on by default for
`ollama`), applying the same "no giant AI node" principle inside stage 2:

1. **Economic read** — one call for the judgement with no per-asset numbers: interpretation, central-bank lean, risk
   regime, causal chain, macro-state nudges, and *which* assets the event actually moves. Retried once if the
   summary merely restates the event.
2. **Per-asset scoring** — one narrow call per affected asset, given the read and that asset's prior from
   `economic_asset_map`, returning just three horizon scores and a rationale for that asset. If a score contradicts
   the model's own risk-regime call, it is retried once with the contradiction stated; the retry is kept only if it
   resolves the conflict or explains itself.

Measured on the same tariff event: the single call produced 8 assets, 3 consistency flags and bullish equities under
a risk-off read; decomposed it produced 2 assets, coherent signs and **0 flags**, in ~2.5 min. Opus-class models keep
the single call (faster, and they handle the full schema).

### Choosing a local model

`scripts/compare_models.py` re-analyses the *same* real events under different models and prints measured quality, so
model choice is an experiment rather than a guess:

```bash
python3 scripts/compare_models.py llama3.1:8b qwen2.5:7b --events 4
```

It rewrites `EXTRACT_MODEL`/`ANALYST_MODEL`, restarts the engine, forces a fresh analysis of each sampled event, and
reports flags per analysis, assets scored per analysis and seconds per analysis, restoring your original values at the
end. Both variables are set together on purpose — see the memory note below.

**Sizing on this machine (M1 Pro / 16 GB).** With Docker Desktop holding a 7.8 GB VM for Postgres, n8n and the engine,
roughly 1.5 GB of RAM was free and swap was 13 GB deep while llama3.1:8b was resident. A 14B model (~9 GB) is not
runnable in that envelope — it wedges the host rather than merely running slowly. So keep one chat model resident at a
time (extractor and analyst set to the same id) and stay in the 7-8B / ~5 GB class, unless you first reduce Docker
Desktop's memory allocation to free headroom. `docker builder prune -f` is worth running before pulling a model; it
reclaimed 16.8 GB here.

Whatever the provider, disagreements are still **measured rather than hidden**: `app/consistency.py` checks every
analysis and stores flags (`risk_regime_sign`, `zero_with_direction`, `duplicate_rationale`,
`summary_repeats_event`), surfaced in `GET /analysis-quality`, `scripts/status.py` and the daily brief. Nothing
rewrites model output — a retry asks again with the problem named; an unresolved disagreement is recorded, not
smoothed over.

**Model prose is never published unchecked.** llama3.1:8b wrote a fluent brief paragraph asserting "the BOJ's
decision to hike rates" — an event nowhere in its input. `app/grounding.py` now verifies that every proper noun and
number in the narrative appears in the data the model was given; if not, the paragraph is withheld and the brief says
so. The narrative is therefore enabled for every provider, and safe because it is checked rather than trusted
(`BRIEF_USE_AI=false` disables it outright). Every other section is computed from the database.

```bash
python3 scripts/status.py           # health, sources delivering, macro state, newest events
python3 scripts/smoke.py            # synthetic hot-CPI print through the whole pipeline (isolated, see below)
bash scripts/test.sh                # unit + database tests in the container (scratch database eco_tests)
docker compose logs --tail=100 engine n8n
```

The smoke run never touches production data: it starts a throwaway engine container on 127.0.0.1:8021 with
`AI_PROVIDER=mock` against a scratch `eco_smoke` database, runs the scenario, and drops both. While the key is
missing, workflows **Eco 08** and **Eco 09** show red executions in n8n every tick (the engine answers 503
`ai_not_configured`) — that is the intended signal, not a bug; they turn green once the key is set.

`docker compose down -v` deletes the database and the n8n state; also remove `.local/workflows-installed` before the
next start. Back up `.env` with the volumes — the encryption key is needed to decrypt n8n credentials.

## How it works

1. **Collect** — n8n workflows 01–07 poll the feeds in [`sources/registry.json`](sources/registry.json) and POST
   canonical batches (`{source, items:[{headline,url,externalId,publishedAt,content,publisher}]}`) to the engine. The
   workflow JSON is *generated* by `scripts/setup.py` from the registry, so adding a source is a JSON edit + re-run of
   `scripts/start.sh` (it re-imports when a template is newer than the install marker). Feeds whose servers reject the
   RSS node's headers (BEA answers 406) take `"fetch": "http"` and are fetched with browser headers + the XML node.
2. **Normalize + dedupe** — every item becomes one `raw_articles` row: UTC timestamp, cleaned text, source
   reliability (0–100), keyword priors for category/asset/country and an importance prior. The `content_hash`
   (source + guid/url/headline) is the duplicate gate; stale items (> 72 h) are stored but not processed.
3. **Extract (stage 1)** — the extractor model with a strict JSON schema (Ollama's grammar-constrained `format`,
   or Anthropic structured outputs) turns the item into facts: event
   type, subject, date, countries, actual/forecast/previous, surprise, tone, importance, "is this reaction coverage".
   Irrelevant items are marked `ignored` before any reasoning is spent.
4. **Cluster** — "Fed holds rates" from 20 outlets is one event. Match order: exact `event_key`
   (`type|country|date[|subject]`) → pgvector cosine similarity over event embeddings within ±48 h → lexical Jaccard
   fallback. Facts merge by source reliability; the official source becomes the primary.
5. **Analyze (stage 2)** — the analyst model receives the event,
   its coverage, the current macro state, the previous three events of the same type, related memory from pgvector,
   same-day release prints and the `economic_asset_map` priors. It returns the summary, economic interpretation,
   central-bank implication, risk-regime read, a causal chain, per-asset × per-horizon impact scores, macro-state
   nudges and key risks, and is checked for self-consistency. Analyses are versioned; new informative coverage
   triggers re-analysis after a debounce.
6. **Macro state** — each `region × dimension` score (-100..100) moves by `old·(1-w) + target·w`, where `w` is a
   function of event importance, analyst confidence and source reliability (0.05–0.60). Labels and trends are derived
   deterministically; every change is journaled in `macro_state_history`.
7. **Market reactions** — when an analysis lands, BTC/ETH prices are anchored (CoinGecko; Gold/EURUSD via Alpha
   Vantage when `REACTIONS_FX_ENABLED=true`) and measured at 5m/15m/1h/4h/24h against the expected direction:
   `CONFIRMED / REJECTED / FLAT`.
8. **Briefs + delivery** — `POST /briefs/daily` (06:00 UTC by workflow 11) renders a pipeline status line, the macro
   regime (traffic lights; collapsed until the first analysis), HIGH releases in the next 48 h, analyzed developments,
   official-source items and the top headlines of the day by keyword signal, asset pressure and risks; the narrative
   paragraph is model-written when a key is configured. Telegram receives it as a fixed-width block.
   Briefs and high-importance alerts are delivered to Telegram through the `notifications` outbox (workflow 12 retries).
   `OUTPUT_LANGUAGE` picks the language of that delivered text (see below); storage stays English.

### Asset universe and scoring

`USD EURUSD XAUUSD BTC ETH SPX NASDAQ US10Y OIL`. Scores run -100..100, negative = bearish. **`US10Y` is the yield**:
BULLISH means the yield rises. Horizons: immediate 0–4 h, short 1–5 trading days, medium 2–8 weeks. `/macro/current`
blends impacts with exponential decay (half-lives 12 h / 3 d / 14 d) and damps thin evidence.

### The per-asset outlook

A score on its own is not information a reader can act on, so `app/outlook.py` turns each asset's aggregate into the
read an analyst would say out loud, and both the brief and the event alerts deliver it that way:

```text
Gold (XAUUSD) — leaning lower, strongest now and fading over the following weeks
   Next 4h -52  ·  1-5 days -40  ·  2-8 weeks -14   (gold price higher)
   Why: Real yields rise with the repriced policy path, and gold pays no coupon to offset that.
   from "US CPI 3.4% y/y, above the 3.1% consensus" (importance 95)
   Evidence solid, from 4 analyzed event(s) · the engine's past calls here: 7 of 11 confirmed by the actual
   price move (30d)
```

- **direction** in words, taken from the horizon that carries the move — an event that does nothing in the first
  hours and moves an asset all week reads "leaning lower", not "no clear direction"
- **path** — `FADING` / `BUILDING` / `STEADY` / `FLIPPING`, because a knee-jerk that decays is a different read from
  one that compounds
- **why** — the analyst's own per-asset rationale, delivered verbatim; the prompts require it to name that asset's
  channel and to say why the pressure fades, builds or reverses
- **what "up" means**, spelled out every time, because `US10Y` positive is the yield rising and bond prices falling
- **evidence** and, once there are at least 5 directional checks in `market_reactions`, **the engine's own hit
  rate** — a call is worth what its history says it is worth, so `outlook.track_record` travels with the read
- assets below a material threshold are named on one line ("Flat, nothing to act on: …") rather than padded out

Nothing here computes new judgement: the numbers are `intel.asset_bias`, the reasons are the analyst's, and the
payload keeps the English text the trading engines read.

## Telegram delivery

Create a bot with @BotFather, put its token in `.env` as `TELEGRAM_BOT_TOKEN`, send the bot `/start` (or add it to a
group or channel as admin), then:

```bash
python3 scripts/telegram_setup.py        # discovers the chat id, writes TELEGRAM_CHAT_ID, restarts the engine, sends a test
```

What arrives: the **daily macro brief** at 06:00 UTC (workflow 11) and an **alert for every event whose first analysis
lands with importance ≥ `TELEGRAM_ALERT_MIN_IMPORTANCE`** (default 80: FOMC/ECB decisions, CPI, NFP, PCE, major
regulation or exchange failures). Messages go through the `notifications` outbox: the engine sends immediately and
workflow **Eco 12** retries anything pending every minute (`POST /notify/flush`). `GET /notify/status` and
`scripts/status.py` show sent/pending/failed counts; `POST /briefs/daily` re-sends today's brief.

### Delivery language

`OUTPUT_LANGUAGE=km` delivers the Telegram brief and the event alerts in Khmer (`en` is the default; an unsupported
value falls back to `en`). Only the delivered text moves — `raw_articles`, `event_analysis`, `briefs.text` and every
`/intel` response stay English, because the gold/forex/crypto engines consume that API and the groundedness gate reads
English prose. `GET /health` reports `delivery.language`.

Two layers, deliberately different in kind:

- **labels** — headings, field names, macro-state vocabulary and the analyst's enums come from static tables in
  `app/i18n.py`. No model involved, so there is nothing to invent. `state_label` translates *by position* in the
  dimension's vocabulary: `TIGHT` is a tight labour market for `employment` and a squeeze for `liquidity`.
  The outlook's own vocabulary (direction of travel, path shape, weight of evidence, instrument names and what
  "up" means for each) is a separate table from the enum table on purpose: `NEUTRAL` is "neutral" as an inflation
  read and "no clear direction" as a direction of travel, so one table cannot serve both. English is a delivery
  language too — the stored enums are for the API, and a reader sees "more hawkish", not `MORE_HAWKISH`.
- **prose** — the engine's own model-written text (brief narrative, event summaries, key risks, causal chain, and
  each asset's rationale in the outlook) goes
  through one `TRANSLATE_MODEL` call (`claude-haiku-4-5`) per message, cached in-process. This needs
  `ANTHROPIC_API_KEY` **whatever `AI_PROVIDER` is**: llama3.1:8b is not good enough at Khmer to publish. Without a
  key, or if the call fails, labels are still translated and each prose segment falls back to its English text.

Headlines, event titles and source names are delivered in the source's own words — they are quotations, not engine
output. The Khmer brief is sent as plain text rather than a `<pre>` block: no proportional script sits on a character
grid, so the localized render uses separators instead of column padding.

**The groundedness gate stays English on purpose.** `app/grounding.py` finds a model's claims by matching capitalised
proper nouns, which Khmer script does not have — a Khmer narrative would pass it blind. So the narrative is gated in
English (`app/briefs._narrative`) and translated only on the way out.

## Publishing: channel posts and page content

The brief and the alerts above are written for the operator: pipeline counts, event ids, model names, a fixed-width
layout inside a `<pre>` block. A public reader needs the same intelligence written as news. `app/social.py` renders
it that way — a hook, the state of play, what it means market by market with the sign spelled out, the calendar,
what would change the read, and the engine's own evidence in place of its plumbing:

```
🌍 GLOBAL MACRO · 2026-09-09 · 06:00 UTC

The third upside CPI surprise in a row has done what the first two could not: it moved the policy path.

📊 THE STATE OF PLAY
🟡 US inflation — above target and rising ↑ (+35)
🔴 GLOBAL risk appetite — risk-off and falling ↓ (-22)
Overall: risk appetite is risk-off, and global liquidity is deteriorating.

💥 WHAT IT MEANS FOR MARKETS
🔽 Gold (XAUUSD) — leaning lower, strongest now and fading over the following weeks
   Next 4h -52 · 1-5 days -40 · 2-8 weeks -14 · (+ = gold price higher)
   Why: Real yields rise and gold pays no coupon to offset that.

📅 ON THE CALENDAR · NEXT 48H
🇺🇸 Fri 12:30 UTC — USD Core PCE Price Index m/m · fcst 0.3% · prev 0.2%

⚠️ WHAT WOULD CHANGE THIS
• A cooler core print on Friday would undo most of the repricing.

📡 Read from 341 items across 18 sources in the last 24h.
🎯 The engine's past calls on XAUUSD: 12 of 17 confirmed by the actual price move (30d).
ℹ️ Economic impact analysis — not trading advice, no positions and no price targets.

#Gold #USD #Bitcoin #Inflation #CentralBanks #Macro #Markets #Cambotix
```

Two platforms, same words: `platform=telegram` returns HTML (bold headings) and `platform=facebook` returns plain
text that pastes anywhere — a Facebook page, X, LinkedIn, WhatsApp. Every post is built from the same data the brief
is built from, so **nothing in a post is written for effect**: the lede is either the brief's narrative (already
through `app/grounding.py`) or an analyst's own summary, the market lines are the stored scores and the analyst's
rationale, and the track record is the measured one from `market_reactions` — withheld entirely rather than implied
when there are too few checks. No model is asked for a headline. Hashtags stay Latin-script English in every
delivery language, because that is how a reader finds the rest of the feed.

**Getting the text** (no posting, no credentials needed):

```bash
curl -s -H "X-Eco-Token: $ENGINE_TOKEN" 'http://127.0.0.1:8020/social/daily?platform=facebook&format=text'
curl -s -H "X-Eco-Token: $ENGINE_TOKEN" "http://127.0.0.1:8020/social/event/$EVENT_ID?platform=facebook&format=text"
```

`GET /social/daily` rebuilds today's picture from the database with **no model call** — it reuses the narrative the
stored brief already published, and re-checks it against the current data before reusing it. Add `lang=km` (or set
`OUTPUT_LANGUAGE`) for a Khmer page; tickers, numbers and hashtags stay as they are. `POST /briefs/daily` also
returns both posts under `social`.

**Posting to a Telegram channel**: create the channel, add the bot as an admin, put its id in `.env` as
`TELEGRAM_CHANNEL_ID` (`@name` or the `-100…` id) and restart the engine. From then on the 06:00 UTC brief run posts
to the channel as well as to the operator's chat, and every event analyzed at importance ≥ `SOCIAL_MIN_IMPORTANCE`
(default 85 — deliberately above the private alert's 80, because a publication is more selective) is posted as
breaking news. Both ride the same `notifications` outbox with their own `target`, so a failed post is retried by
workflow Eco 12 like everything else. `POST /social/publish?kind=daily` (or `kind=event&event_id=…`) posts on
demand — useful if the channel should publish at a different hour than the private brief.

**A Facebook page is not posted to from the engine**: it holds no page credentials and asks for none. Serve
`platform=facebook` to whatever does — an n8n Facebook Graph node, a Buffer/Zapier hook, or a copy-paste.

## Intelligence API

All routes except `/health` need the header `X-Eco-Token: <ENGINE_TOKEN>` (from `.env`). Bound to 127.0.0.1:8020.

| Route | Purpose |
|---|---|
| `GET /macro/current` | Risk regime, global liquidity, every region×dimension state, per-asset macro bias with drivers |
| `GET /macro/history?region=US&dimension=inflation` | Journal of state changes |
| `GET /events/recent?hours=24&min_importance=40` | Analyzed events with summaries |
| `GET /events/upcoming?hours=48&min_impact=HIGH` | Calendar releases (forecast/previous/actual) |
| `GET /events/{id}` | Event, linked articles by role, latest analysis, asset impacts, market reactions |
| `GET /analysis/{asset}` | Decay-weighted bias, contributing events with rationale, reaction scorecard |
| `GET /briefs/latest?format=text` | Newest daily brief |
| `GET /sources` | Registry with article counts |
| `GET /analysis-quality?days=7` | Analyses, self-consistency flags and average confidence per model — the measurement to consult before trusting a local model's scores |
| `GET /social/daily?platform=facebook&format=text` | Today's brief as content to post (`platform=telegram` for HTML, `lang=km` for Khmer) |
| `GET /social/event/{id}?platform=facebook` | One analyzed event as a breaking-news post |
| `POST /social/publish?kind=daily` | Post to the Telegram channel now (`kind=event&event_id=…` for one event) |
| `GET /notify/status`, `POST /notify/flush`, `POST /notify/test` | Telegram outbox |
| `POST /ingest/articles`, `/ingest/calendar` | Called by n8n collectors |
| `POST /process/extract?batch=5`, `/process/analyze?force=false`, `/process/reactions`, `/briefs/daily` | Called by n8n schedulers; `force=true` skips the coverage debounce for a manual run |

## Layout

```text
app/            FastAPI engine: pipeline.py (write path), intel.py (read models), ai.py (Ollama + Claude + mock),
                clustering.py, consistency.py, grounding.py, macro_state.py, reactions.py, briefs.py, telegram.py,
                normalize.py, outlook.py (per-asset read as words), i18n.py (delivery language),
                social.py (the same intelligence as a public post), schemas.py, prompts.py, embeddings.py
db/             01-databases.sql (n8n db), 02-schema.sql (engine schema + seeds)
sources/        registry.json — the source registry (reliability, priority, workflow group)
n8n/            generated workflow templates (eco01…eco11), committed for review
scripts/        setup.py · start.sh · status.py · smoke.py · test.sh · telegram_setup.py · native_ollama.py ·
                pull_models.py · compare_models.py
tests/          unit tests (run anywhere) + database tests (scripts/test.sh)
docs/           architecture.md — design notes, data model, roadmap
                trader-value-roadmap.md — what to build next, ranked by value to a reader
```

## Scope and roadmap

V1 (this repo): US macro + major global news + crypto, with ECB as the first non-US central bank.
V2: EU/UK/Japan/China statistics and central banks. V3: commodities, energy, fiscal and global liquidity series
(FRED/BEA/BLS APIs as authoritative backfill). See [docs/architecture.md](docs/architecture.md).

Caveats: the ForexFactory calendar is a ToS grey area — polling is hourly; move to a licensed calendar for anything
beyond experimental use. Alpha Vantage's free tier is ~25 requests/day. The BLS/BEA RSS feeds announce releases
promptly, but the analyst's actual-vs-forecast at release time comes from the calendar print, not the BLS API.
