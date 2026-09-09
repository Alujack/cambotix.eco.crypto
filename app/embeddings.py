"""Embeddings for pgvector. Any failure degrades to 'no vector' — the pipeline keeps working without memory search."""
import logging

import httpx

from app.config import env, ollama_base_url

log = logging.getLogger('eco.embeddings')
DEFAULT_MODELS = {'openai': 'text-embedding-3-small', 'voyage': 'voyage-3-lite', 'ollama': 'nomic-embed-text'}
_warned = set()


def provider() -> str:
    name = env('EMBEDDINGS_PROVIDER', 'ollama').strip().lower()
    if name == 'openai' and not env('OPENAI_API_KEY'):
        return 'none'
    if name == 'voyage' and not env('VOYAGE_API_KEY'):
        return 'none'
    return name if name in DEFAULT_MODELS else 'none'


def enabled() -> bool:
    return provider() != 'none'


def model_name() -> str | None:
    name = provider()
    if name == 'none':
        return None
    return env('EMBEDDINGS_MODEL') or DEFAULT_MODELS[name]


def embed(texts: list[str]) -> list[list[float]] | None:
    name = provider()
    if name == 'none' or not texts:
        return None
    model = model_name()
    try:
        with httpx.Client(timeout=30) as client:
            if name == 'openai':
                response = client.post('https://api.openai.com/v1/embeddings', json={'model': model, 'input': texts},
                                       headers={'Authorization': 'Bearer ' + env('OPENAI_API_KEY')})
                response.raise_for_status()
                return [item['embedding'] for item in response.json()['data']]
            if name == 'voyage':
                response = client.post('https://api.voyageai.com/v1/embeddings',
                                       json={'model': model, 'input': texts, 'input_type': 'document'},
                                       headers={'Authorization': 'Bearer ' + env('VOYAGE_API_KEY')})
                response.raise_for_status()
                return [item['embedding'] for item in response.json()['data']]
            response = client.post(ollama_base_url() + '/api/embed',
                                   json={'model': model, 'input': texts})
            response.raise_for_status()
            return response.json()['embeddings']
    except (httpx.HTTPError, KeyError, ValueError) as error:
        if name not in _warned:
            _warned.add(name)
            log.warning('embeddings via %s unavailable (%s); continuing without vector memory', name, error)
        return None


def to_pgvector(vector: list[float]) -> str:
    return '[' + ','.join(f'{value:.6f}' for value in vector) + ']'


def store(conn, kind: str, ref_id: str, text: str, vector: str, event_id: str | None = None) -> None:
    conn.execute('''INSERT INTO knowledge_embeddings (kind, ref_id, model, embedding, text, event_id)
                    VALUES (%s, %s, %s, %s::vector, %s, %s)
                    ON CONFLICT (kind, ref_id, model) DO UPDATE SET embedding = EXCLUDED.embedding, text = EXCLUDED.text,
                        event_id = EXCLUDED.event_id, created_at = now()''',
                 (kind, ref_id, model_name(), vector, text[:4000], event_id))


def similar(conn, kinds: list[str], vector: str, limit: int = 6, exclude_event_id: str | None = None) -> list[dict]:
    return conn.execute('''
        SELECT kind, ref_id, event_id, text, created_at, 1 - (embedding <=> %s::vector) AS similarity
        FROM knowledge_embeddings
        WHERE kind = ANY(%s) AND model = %s AND (event_id IS DISTINCT FROM %s)
        ORDER BY embedding <=> %s::vector LIMIT %s''',
        (vector, kinds, model_name(), exclude_event_id, vector, limit)).fetchall()
