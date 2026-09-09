#!/usr/bin/env bash
# Unit tests run everywhere; database tests run inside the engine container against a scratch eco_tests database.
set -euo pipefail
cd "$(dirname "$0")/.."
# The image bakes in app/ and tests/, so without a rebuild this would test the code as of the last build, silently.
docker compose up -d --build --wait engine >/dev/null
docker compose exec -T postgres psql -U eco -d postgres -v ON_ERROR_STOP=1 -c 'DROP DATABASE IF EXISTS eco_tests;' -c 'CREATE DATABASE eco_tests;' >/dev/null
docker compose exec -T postgres psql -U eco -d eco_tests -v ON_ERROR_STOP=1 -q < db/02-schema.sql >/dev/null
docker compose exec -T -e AI_PROVIDER=mock -e EMBEDDINGS_PROVIDER=none \
  -e ECO_DATABASE_URL='postgresql://eco:'"$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)"'@postgres:5432/eco_tests' \
  engine python -m pytest tests -q "$@"
