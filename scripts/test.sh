#!/usr/bin/env bash
# Unit tests run everywhere; database tests run inside the engine container against a scratch eco_tests database.
set -euo pipefail
cd "$(dirname "$0")/.."
# Only the database is needed running; the tests get their own throwaway container. `up --build --wait engine`
# recreated the *live* engine, which dropped the collectors mid-poll (ECONNREFUSED in the n8n log) and discarded the
# engine's log history - the one place a missed brief would have been diagnosable.
docker compose up -d --wait postgres >/dev/null
# The image bakes in app/ and tests/, so without a rebuild this would test the code as of the last build, silently.
docker compose build engine >/dev/null
docker compose exec -T postgres psql -U eco -d postgres -v ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS eco_tests;' -c 'CREATE DATABASE eco_tests;' >/dev/null
docker compose exec -T postgres psql -U eco -d eco_tests -v ON_ERROR_STOP=1 -q < db/02-schema.sql >/dev/null
# `run --rm --no-deps` publishes no ports, so this never collides with the running engine on ENGINE_PORT. Telegram
# is blanked for the same reason scripts/smoke.py blanks it: the service loads .env and holds the real bot token.
docker compose run --rm --no-deps -T -e AI_PROVIDER=mock -e EMBEDDINGS_PROVIDER=none \
  -e TELEGRAM_BOT_TOKEN= -e TELEGRAM_CHAT_ID= -e TELEGRAM_CHANNEL_ID= \
  -e ECO_DATABASE_URL='postgresql://eco:'"$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"'@postgres:5432/eco_tests' \
  engine python -m pytest tests -q "$@"
