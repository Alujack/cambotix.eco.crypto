#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/setup.py
docker compose up -d --build --wait
needs_import=0
if [ ! -f .local/workflows-installed ]; then needs_import=1; fi
# Re-import when the registry/generator changed a template after the last install.
if [ -f .local/workflows-installed ] && [ -n "$(find n8n -name '*.json' -newer .local/workflows-installed | head -1)" ]; then needs_import=1; fi
if [ "$needs_import" = 1 ]; then
  docker compose exec -T -u root n8n n8n import:credentials --input=/imports/credentials.json
  docker compose exec -T -u root n8n n8n import:workflow --input=/imports/workflows.json
  # A for-loop, not `while read`: `docker compose exec` would otherwise consume the rest of the id list from stdin.
  for workflow in $(cat .local/workflow-ids.txt); do
    docker compose exec -T -u root n8n n8n publish:workflow --id="$workflow" </dev/null
  done
  expected=$(grep -c . .local/workflow-ids.txt)
  ids=$(paste -sd, .local/workflow-ids.txt | sed "s/,/','/g")
  published=$(docker compose exec -T postgres psql -U eco -d n8n -tAc "SELECT count(*) FROM workflow_entity WHERE id IN ('$ids') AND \"activeVersionId\" IS NOT NULL")
  if [ "$published" != "$expected" ]; then
    echo "Workflow publishing did not complete ($published/$expected). Review the n8n import output." >&2
    exit 1
  fi
  docker compose restart n8n
  docker compose up -d --wait
  touch .local/workflows-installed
fi
rm -f .local/import/credentials.json
python3 scripts/status.py
