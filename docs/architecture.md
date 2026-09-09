# Architecture notes

## Principle

The engine answers *what happened, why, is it new, how important, inflationary or not, hawkish or dovish, risk-on or
risk-off, liquidity up or down, which countries/currencies/assets, confirming or contradicting the trend, immediate or
slow* — and nothing about entries, stops or sizing. Trading engines consume `GET /macro/current`, `/events/*` and
`/analysis/{asset}`; they never read the raw tables.

n8n orchestrates; the LLM reasons. Finding information is deterministic (feeds, hashes, registry); interpreting it is
the model's job, in two stages so the expensive model only ever sees clustered events, not every headline.

## Pipeline

| Step | Where | Trigger |
|---|---|---|
| Collect RSS / API | n8n `eco01`–`eco06` | every 5–60 min |
| Calendar | n8n `eco07` → `POST /ingest/calendar` | hourly; a HIGH-impact print spawns a synthetic `release_print` article |
| Normalize, hash, dedupe, priors | `app/pipeline.ingest_articles` | on POST |
| Extract facts | `app/pipeline.run_extract` (`ai.extract`, Haiku) | n8n `eco08` every 30 s, batch 5 |
| Cluster into events | `app/clustering.find_event` | inside extract |
| Analyze | `app/pipeline.run_analyze` (`ai.analyze`, Opus, adaptive thinking) | n8n `eco09` every 60 s, one event per tick |
| Macro-state update | `app/macro_state.apply_updates` | inside analyze, same transaction |
| Reaction anchors | `app/reactions.open_windows` | inside analyze (first version only) |
| Reaction measurement | `app/reactions.measure_due` | n8n `eco10` every 5 min |
| Daily brief | `app/briefs.build_daily` | n8n `eco11` 06:00 UTC |

Queue semantics: `raw_articles.status` (`queued → extracting → extracted | ignored | error`) and
`economic_events.needs_analysis` with `FOR UPDATE SKIP LOCKED` claims, so several n8n ticks can overlap safely.
Failures back off (`next_attempt_at`) and give up after 3 (articles) / 5 (events) attempts. An unconfigured AI provider
requeues without counting an attempt and returns 503, which shows red in n8n.

Analysis gating: `importance ≥ ANALYZE_MIN_IMPORTANCE` (40), coverage quiet for `ANALYZE_DEBOUNCE_SECONDS` (180) unless
importance ≥ 80, and re-analysis at most every `REANALYZE_MIN_SECONDS` (900) when new *informative* (non-reaction)
coverage arrives.

## Data model

```text
sources ──< raw_articles >── event_articles >── economic_events ──< event_analysis ──< asset_impacts
                                                      │                    │
                                                      ├──< market_reactions│
                                                      ├──< economic_releases (calendar, linked when analyzed)
                                                      └──< knowledge_embeddings (pgvector; kind = article|event|analysis)
macro_state (region × dimension) ──< macro_state_history      economic_asset_map (priors)      briefs
```

Regions × dimensions: US (inflation, employment, growth, monetary_policy, liquidity, fiscal), EU (inflation, growth,
monetary_policy), GLOBAL (risk_appetite, geopolitical_risk, liquidity, energy), CRYPTO (regulation, adoption,
market_structure). Score sign convention: +1 = hotter inflation, stronger jobs/growth, more restrictive policy, looser
liquidity, more expansionary fiscal, more risk appetite, higher geopolitical risk, tighter energy, more supportive
regulation, faster adoption, healthier market structure.

Vector column has no fixed dimension so the embedding provider can change; queries always filter by `model`. Add an
HNSW index once the provider is settled: `CREATE INDEX ON knowledge_embeddings USING hnsw ((embedding::vector(1536))
vector_cosine_ops)` (dimension per model).

## Source reliability (initial)

Official (Fed, ECB, BLS, BEA, SEC, CFTC) 100 · Reuters/Bloomberg 95 · WSJ/FT 90 · CNBC 80 · MarketWatch 75 ·
Alpha Vantage aggregate 70 (publisher overrides) · ForexFactory calendar 70 · CoinDesk/The Block/ForexLive 65 ·
Cointelegraph 60 · Decrypt 55. Reliability decides which article's facts win inside an event and scales the macro-state
weight; it is not a truth score.

## Adding a source

Add an entry to `sources/registry.json` (`key`, `url`, `category`, `country`, `priority`, `reliability`,
`default_categories`, `workflow` group) and run `bash scripts/start.sh`. The generator puts RSS feeds on an RSS Feed
Read node named after the key; `"fetch": "http"` switches that feed to HTTP Request (browser `User-Agent`/`Accept`) →
XML → `Normalize XML feed`, for servers such as apps.bea.gov that answer 406 to rss-parser. The engine seeds the
`sources` table from the same file on start, so the key is known before the first batch arrives.

## Event clustering

`event_key = TYPE|COUNTRY|EVENT_DATE[|subject-slug]`. Releases (CPI, NFP, FOMC decision …) cluster on type+date alone;
speeches, regulatory actions, incidents include the subject. Anything that misses the key match is tried against event
embeddings (cosine ≥ 0.86, same category, ±48 h) and finally against titles of same-type events (Jaccard ≥ 0.45,
±36 h). Reaction coverage ("BTC falls after CPI") links to the event with role `reaction` and never overwrites facts.

## Roadmap

- V1 (now): US macro + major global + crypto; ECB press releases. Manual review of analyses through `/events/{id}`.
- V2: EU/UK/JP/CN statistics offices and central banks (BoE, BoJ, PBoC feeds), Eurostat flash estimates, Trading
  Economics or another licensed calendar, Telegram delivery of the daily brief.
- V3: FRED/BEA/BLS API backfill of series into `economic_releases`, energy/commodity feeds, fiscal trackers, global
  liquidity (M2, Fed balance sheet, TGA/RRP), weekly brief, evaluation of analyst calls against `market_reactions`.
