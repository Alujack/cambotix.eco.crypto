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

This generates `.env` secrets, starts Postgres (pgvector), n8n and the engine, imports and publishes the eleven n8n
workflows, and prints a status view. Open **http://localhost:5681** and create the local n8n owner account — the
workflows are already active. Existing n8n installs on 5678/5679/5680 are unaffected.

Then put your key in `.env` and restart the engine:

```bash
ANTHROPIC_API_KEY=sk-ant-...        # required for extraction + analysis (collection works without it)
OPENAI_API_KEY=sk-...               # optional: enables pgvector memory (embeddings). Or EMBEDDINGS_PROVIDER=voyage|ollama
ALPHA_VANTAGE_API_KEY=...           # optional: hourly aggregated news (free tier ≈ 25 req/day)
docker compose up -d engine
```

Without an Anthropic key the collectors keep filling `raw_articles`; `/process/extract` and `/process/analyze` return
503 until the key is set (or `AI_PROVIDER=mock` for an offline run).

```bash
python3 scripts/status.py           # health, sources delivering, macro state, newest events
python3 scripts/smoke.py            # synthetic hot-CPI print through the whole pipeline
bash scripts/test.sh                # unit + database tests in the container
docker compose logs --tail=100 engine n8n
```

`docker compose down -v` deletes the database and the n8n state; also remove `.local/workflows-installed` before the
next start. Back up `.env` with the volumes — the encryption key is needed to decrypt n8n credentials.

## How it works

1. **Collect** — n8n workflows 01–07 poll the feeds in [`sources/registry.json`](sources/registry.json) and POST
   canonical batches (`{source, items:[{headline,url,externalId,publishedAt,content,publisher}]}`) to the engine. The
   workflow JSON is *generated* by `scripts/setup.py` from the registry, so adding a source is a JSON edit + re-run.
2. **Normalize + dedupe** — every item becomes one `raw_articles` row: UTC timestamp, cleaned text, source
   reliability (0–100), keyword priors for category/asset/country and an importance prior. The `content_hash`
   (source + guid/url/headline) is the duplicate gate; stale items (> 72 h) are stored but not processed.
3. **Extract (stage 1, cheap model)** — `claude-haiku-4-5` with a strict JSON schema turns the item into facts: event
   type, subject, date, countries, actual/forecast/previous, surprise, tone, importance, "is this reaction coverage".
   Irrelevant items are marked `ignored` before any reasoning is spent.
4. **Cluster** — "Fed holds rates" from 20 outlets is one event. Match order: exact `event_key`
   (`type|country|date[|subject]`) → pgvector cosine similarity over event embeddings within ±48 h → lexical Jaccard
   fallback. Facts merge by source reliability; the official source becomes the primary.
5. **Analyze (stage 2, reasoning model)** — `claude-opus-5` (adaptive thinking, structured output) receives the event,
   its coverage, the current macro state, the previous three events of the same type, related memory from pgvector,
   same-day release prints and the `economic_asset_map` priors. It returns the summary, economic interpretation,
   central-bank implication, risk-regime read, a causal chain, per-asset × per-horizon impact scores, macro-state
   nudges and key risks. Analyses are versioned; new informative coverage triggers re-analysis after a debounce.
6. **Macro state** — each `region × dimension` score (-100..100) moves by `old·(1-w) + target·w`, where `w` is a
   function of event importance, analyst confidence and source reliability (0.05–0.60). Labels and trends are derived
   deterministically; every change is journaled in `macro_state_history`.
7. **Market reactions** — when an analysis lands, BTC/ETH prices are anchored (CoinGecko; Gold/EURUSD via Alpha
   Vantage when `REACTIONS_FX_ENABLED=true`) and measured at 5m/15m/1h/4h/24h against the expected direction:
   `CONFIRMED / REJECTED / FLAT`.
8. **Briefs** — `POST /briefs/daily` (06:00 UTC by workflow 11) renders the macro regime, high-impact releases,
   developments, asset pressure and risks; the narrative paragraph is model-written when a key is configured.

### Asset universe and scoring

`USD EURUSD XAUUSD BTC ETH SPX NASDAQ US10Y OIL`. Scores run -100..100, negative = bearish. **`US10Y` is the yield**:
BULLISH means the yield rises. Horizons: immediate 0–4 h, short 1–5 trading days, medium 2–8 weeks. `/macro/current`
blends impacts with exponential decay (half-lives 12 h / 3 d / 14 d) and damps thin evidence.

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
| `POST /ingest/articles`, `/ingest/calendar` | Called by n8n collectors |
| `POST /process/extract`, `/process/analyze`, `/process/reactions`, `/briefs/daily` | Called by n8n schedulers |

## Layout

```text
app/            FastAPI engine: pipeline.py (write path), intel.py (read models), ai.py (Claude + mock),
                clustering.py, macro_state.py, reactions.py, briefs.py, normalize.py, schemas.py, prompts.py
db/             01-databases.sql (n8n db), 02-schema.sql (engine schema + seeds)
sources/        registry.json — the source registry (reliability, priority, workflow group)
n8n/            generated workflow templates (eco01…eco11), committed for review
scripts/        setup.py · start.sh · status.py · smoke.py · test.sh
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
