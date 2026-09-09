import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parents[1] / 'sources' / 'registry.json'


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text())


def seed(conn) -> int:
    registry = load_registry()
    for source in registry['sources']:
        conn.execute('''INSERT INTO sources (key, name, category, country, type, url, priority, reliability, enabled,
                                             default_categories, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, category = EXCLUDED.category,
                            country = EXCLUDED.country, type = EXCLUDED.type, url = EXCLUDED.url,
                            priority = EXCLUDED.priority, reliability = EXCLUDED.reliability, enabled = EXCLUDED.enabled,
                            default_categories = EXCLUDED.default_categories, updated_at = now()''',
                     (source['key'], source['name'], source['category'], source.get('country') or 'GLOBAL', source['type'],
                      source.get('url'), source.get('priority', 50), source.get('reliability', 50),
                      source.get('enabled', True), source.get('default_categories') or []))
    return len(registry['sources'])


def publisher_reliability(name: str | None, fallback: int) -> int:
    if not name:
        return fallback
    overrides = load_registry().get('publisher_reliability', {})
    for known, value in overrides.items():
        if known.lower() in name.lower():
            return int(value)
    return fallback
