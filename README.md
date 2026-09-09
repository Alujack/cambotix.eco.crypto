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

Measured on an M1 Pro / 16 GB with `llama3.1:8b`: **~25 s per article** for extraction, **~2 min per event** for
analysis. That comfortably outpaces arrivals (~15 articles/hour from the current feed set). Local inference is serial,
so `/process/extract` and `/process/analyze` hold a lock and answer `{"skipped": "busy"}` if a tick arrives while a
previous one is still running — the n8n executions stay green.

One thing is off by default because of this: the brief's **model-written narrative paragraph**. Running locally,
llama3.1:8b produced a fluent paragraph that asserted "the BOJ's decision to hike rates" — an event that appeared
nowhere in its input. A fabricated macro fact delivered to Telegram as fact is worse than no paragraph, so the
narrative is on for `anthropic`/`mock` and **off for `ollama`** unless you set `BRIEF_USE_AI=true`. Every other
section of the brief is computed from the database and never depends on model prose.

Quality is the real trade-off. An 8B model reliably produces schema-valid output and decent fact extraction, but its
economic reasoning is shallower than the Opus-class analyst this pipeline was designed around, and it sometimes
contradicts itself (calling an event risk-off while scoring equities bullish). The engine therefore **measures** that
rather than hiding it: every analysis is checked for self-consistency and the flags are stored, surfaced in
`GET /analysis-quality`, `scripts/status.py` and the daily brief. Observed flag codes are `risk_regime_sign`
(sign disagrees with the stated risk regime), `zero_with_direction` (a directional rationale scored 0),
`duplicate_rationale` and `summary_repeats_event`. Use that endpoint to decide whether a local model is good enough
for a given job, or to compare models — `ANALYST_MODEL` is the only thing that has to change.

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

### Asset universe and scoring

`USD EURUSD XAUUSD BTC ETH SPX NASDAQ US10Y OIL`. Scores run -100..100, negative = bearish. **`US10Y` is the yield**:
BULLISH means the yield rises. Horizons: immediate 0–4 h, short 1–5 trading days, medium 2–8 weeks. `/macro/current`
blends impacts with exponential decay (half-lives 12 h / 3 d / 14 d) and damps thin evidence.

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
| `GET /analysis-quality?days=7` | Analyses, self-consistency flags and average confidence per model |
| `GET /notify/status`, `POST /notify/flush`, `POST /notify/test` | Telegram outbox |
| `POST /ingest/articles`, `/ingest/calendar` | Called by n8n collectors |
| `POST /process/extract?batch=5`, `/process/analyze?force=false`, `/process/reactions`, `/briefs/daily` | Called by n8n schedulers; `force=true` skips the coverage debounce for a manual run |

## Layout

```text
app/            FastAPI engine: pipeline.py (write path), intel.py (read models), ai.py (Ollama + Claude + mock),
                clustering.py, consistency.py, macro_state.py, reactions.py, briefs.py, telegram.py, normalize.py,
                schemas.py, prompts.py, embeddings.py
db/             01-databases.sql (n8n db), 02-schema.sql (engine schema + seeds)
sources/        registry.json — the source registry (reliability, priority, workflow group)
n8n/            generated workflow templates (eco01…eco11), committed for review
scripts/        setup.py · start.sh · status.py · smoke.py · test.sh · telegram_setup.py · native_ollama.py ·
                pull_models.py
tests/          unit tests (run anywhere) + database tests (scripts/test.sh)
docs/           architecture.md — design notes, data model, roadmap
```

## Scope and roadmap

V1 (this repo): US macro + major global news + crypto, with ECB as the first non-US central bank.
V2: EU/UK/Japan/China statistics and central banks. V3: commodities, energy, fiscal and global liquidity series
(FRED/BEA/BLS APIs as authoritative backfill). See [docs/architecture.md](docs/architecture.md).

Caveats: the ForexFactory calendar is a ToS grey area — polling is hourly; move to a licensed calendar for anything
beyond experimental use. Alpha Vantage's free tier is ~25 requests/day. The BLS/BEA RSS feeds announce releases
promptly, but the analyst's actual-vs-forecast at release time comes from the calendar print, not the BLS API.
