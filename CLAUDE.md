# Cambotix Economic Intelligence Engine

Standalone "economic brain": collect → understand → connect → summarize → score → remember economic information.
It never trades. Gold / forex / crypto engines consume its intelligence API later.

## Stack
- n8n (Docker, own `n8n` database) — collectors on schedules, thin: RSS/HTTP → Code normalize → POST to the engine
- Python 3.12 + FastAPI (`app/`) — ingest, dedupe, extraction, event clustering, analyst, macro state, reactions,
  briefs, public posts, and the intelligence API. Runs as the `engine` service on 127.0.0.1:8020
- PostgreSQL 17 + pgvector (`db/02-schema.sql`) — one database `eco`; vector memory in `knowledge_embeddings`
- AI is provider-switchable via `AI_PROVIDER`: `ollama` (default — Llama 3.1 8B local, grammar-constrained JSON),
  `anthropic` (`claude-haiku-4-5` extractor + `claude-opus-5` analyst, adaptive thinking, structured outputs),
  `mock` (offline stand-in for tests/smoke). `EXTRACT_MODEL`/`ANALYST_MODEL` fall back to the provider's defaults,
  so a stale `claude-*` value never leaks into an Ollama run — see `app/config.model_for`
- Ollama: macOS runs a project-local native server on 127.0.0.1:11436 (`scripts/native_ollama.py`, Metal); other hosts
  use the `docker-ai` compose profile. Embeddings default to local `nomic-embed-text`

## Commands
- `bash scripts/start.sh` — generate `.env`, build, start, import + publish the n8n workflows (first run), print status
- `python3 scripts/status.py` — health, queue, macro state, newest events
- `python3 scripts/smoke.py` — synthetic hot-CPI print through the whole pipeline in a throwaway engine container
  (port 8021, mock AI, scratch database `eco_smoke`); production data is never touched
- `bash scripts/test.sh` — unit + database tests inside the engine container against `eco_tests`
  (rebuilds the image first: app/ and tests/ are baked in, so without it you would test stale code)
- `python3 scripts/telegram_setup.py` — connect the bot: discover chat id, write `.env`, restart engine, send a test
- `python3 scripts/pull_models.py` — make the models named in `.env` present in Ollama
- `python3 scripts/compare_models.py <model> <model>` — re-analyse the same events under each model and report
  flags/assets/seconds per analysis (restores `.env` afterwards)
- `docs/trader-value-roadmap.md` — what to build next for a reader, ranked by value per unit of work
- `docs/telegram-delivery.md` — what Telegram receives and why, with the platform's length and rate limits
- `python3 scripts/setup.py` — regenerate `.local/import/*` and the committed `n8n/*.json` templates
- `docker compose logs --tail=100 engine n8n`

## Rules
- No trading logic here: no entries, stops, sizing, signals. Output is economic impact analysis only.
- UTC everywhere (release times, reaction windows, briefs). Never store naive timestamps.
- Never hardcode secrets; `.env` is generated and gitignored. Workflow JSON references credentials by id only.
- Sources live in `sources/registry.json` (reliability/priority). Add a feed there and rerun `scripts/setup.py`;
  do not hand-edit workflows in the n8n UI — regenerate and re-import.
- LLM calls and Telegram sends never run inside a database transaction (claim → commit → call → write).
- `db/02-schema.sql` must stay idempotent: the engine applies it on every start (that is the migration step).
- Every AI output is validated against `app/schemas.py`; structured-output schemas come from those models.
  Ollama needs `$ref`/`$defs` inlined (`app/ai.inline_refs`) because its decoder takes one flat schema.
- Model prose is never presented as data: the brief's sections are computed, and the narrative is published only if
  `app/grounding.py` finds every proper noun and number in it present in the data the model was given (an 8B model
  fabricated a BOJ rate hike that was not in its input).
- The analyst is decomposed for weak models (`app/config.decompose_analysis`, on for ollama): one economic read, then
  one narrow scoring call per affected asset with a single corrective retry on a sign that contradicts the read.
  Don't collapse it back into one call for local models — that is what produced all-zero scores and duplicated rationales.
- Every Ollama call must use the same `num_ctx` (`app/ai.num_ctx`). Ollama keys its loaded instance on it, so a
  per-stage value reloads the 4.9 GB model between calls and turns a 20 s call into a timeout.
- Never silently rewrite model output. Disagreements go through `app/consistency.py` as recorded flags, so weak-model
  analyses can be discounted downstream instead of looking authoritative.
- Local inference is serial: the extract/analyze endpoints hold a lock and return `skipped: busy` rather than queueing.
- Keep ONE chat model resident: set `EXTRACT_MODEL` and `ANALYST_MODEL` to the same id and stay ~5 GB. On this
  16 GB host Docker Desktop holds a 7.8 GB VM, leaving ~1.5 GB free with swap 13 GB deep — a 14B model wedges it.
- `/health` must stay local (no outbound calls): the container healthcheck runs it every 10 s with a 3 s budget
  and a busy model server made an inline probe fail it. Provider reachability lives on `/ai/status`.
- httpx request logging is pinned to WARNING: it logs full URLs at INFO, which would write the Telegram bot
  token into the container logs on every send.
- Country codes are normalized to ISO-2 (`app/normalize.normalize_countries`) before they reach a clustering key —
  models write "Canada" as often as "CA" and the event key and array matching depend on one spelling.
- `US10Y` scores are yield direction (BULLISH = yield up). Scores are -100..100, negative = bearish.
- Delivered text never shows a bare score. `app/outlook.py` is the layer that says a read the way an analyst would:
  direction in words (from the horizon carrying the move), the path across the three horizons
  (FADING/BUILDING/STEADY/FLIPPING), the analyst's own per-asset rationale verbatim, what "up" means for that
  instrument, the weight of the evidence, and the engine's measured hit rate from `market_reactions` — withheld
  below 5 directional calls, because 2-of-2 reads as skill when it is noise. It computes no judgement of its own.
- The outlook vocabulary is a separate table from the enum vocabulary in `app/i18n.py`. `NEUTRAL` is "neutral" as an
  inflation read and "no clear direction" as a direction of travel; one table cannot serve both. English is a
  delivery language too (`EN_ENUMS`, and `state_label` lowercases): stored enums are for the trading engines that
  read the API, and no reader should have to decode `WELL_ABOVE_TARGET`.
- Telegram gets ONE message. The full brief is ~12k characters, which Telegram splits into four and which buries
  the read under the macro table, the headline lists and the pipeline counters; the operator's chat receives
  `brief['operatorPost']` instead — the `app/social.py` digest plus one line of pipeline health.
  `TELEGRAM_BRIEF_STYLE=full` restores the old body. The complete text is always stored in `briefs` and served by
  `GET /briefs/latest`, so nothing is lost by delivering less.
- Message length is measured in UTF-16 code units, never `len()` (`app/telegram.width`): Telegram's 4096 cap counts
  an emoji as 2 and a flag as 4, so a character count silently under-measures an emoji-dense brief. Chunks are
  paced ~1s apart because Telegram asks senders not to exceed one message per second to a chat.
- Public posts (`app/social.py`) are the same data as the brief, rendered as news: no model writes a headline, the
  lede is prose that already passed a gate (the brief's narrative, or an analyst's summary), and a track record is
  published only when `market_reactions` measured enough windows to support it. Never trading advice: no entries,
  stops, sizing or price targets, and every post carries the disclaimer label. Platform differences are rendering
  only (`telegram` = HTML, `facebook` = plain text); hashtags stay Latin-script English in every language.
- `OUTPUT_LANGUAGE` (`en` default, `km` Khmer) changes **delivered text only** — Telegram messages and the rendered
  brief. The database, the intelligence API and `app/grounding.py` stay English: the trading engines read that API,
  and the groundedness gate matches capitalised proper nouns, which Khmer has none of. Labels come from the static
  tables in `app/i18n.py` (translate `state_label` by position — `TIGHT` differs per dimension); prose is translated
  by one `TRANSLATE_MODEL` call and falls back to English per segment. Headlines and titles stay in the source's words.

## Glossary
- article: one collected item (`raw_articles`), deduped by `content_hash`
- event: one real-world economic event (`economic_events`), many articles; `event_key` = type|country|date[|subject]
- extraction: stage-1 facts (`raw_articles.extraction`); analysis: stage-2 reasoning (`event_analysis`, versioned)
- macro state: living score per region×dimension (`macro_state`), journaled in `macro_state_history`
- asset impact: per-event, per-asset, per-horizon score (immediate 0-4h, short 1-5d, medium 2-8w)
- reaction: what the market actually did at 5m/15m/1h/4h/24h vs the expected direction
- notification: one outbox row (`notifications`) per Telegram message: brief, event_alert, test, social_brief or
  social_event; `target` is the chat or channel it goes to (NULL = the operator's `TELEGRAM_CHAT_ID`)
- post: the brief or one event rendered for a public channel or page (`app/social.py`), served by `/social/*`
- outlook: the delivered per-asset read (`app/outlook.py`) — direction, path, reason, evidence, track record
- track record: how often the engine's expected direction was confirmed by the measured price move (`market_reactions`)
- delivery language: the language of the text Telegram receives (`app/i18n.py`); English remains the storage language
