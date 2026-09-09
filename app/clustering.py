"""Turn many articles into one economic event: exact key, then vector similarity, then lexical fallback."""
import hashlib
import re
from datetime import timedelta

from app.schemas import SUBJECT_EVENT_TYPES, Extraction

STOPWORDS = {'the', 'and', 'for', 'with', 'that', 'this', 'from', 'after', 'over', 'into', 'its', 'are', 'was',
             'has', 'have', 'will', 'says', 'said', 'amid', 'than', 'more', 'less', 'about', 'news', 'update'}


def slug(value: str, limit: int = 40) -> str:
    return re.sub(r'[^a-z0-9]+', '-', (value or '').lower()).strip('-')[:limit]


def event_key(extraction: Extraction) -> str:
    country = (extraction.countries[0] if extraction.countries else 'GLOBAL').upper()
    parts = [extraction.event_type, country, extraction.event_date]
    if extraction.event_type in SUBJECT_EVENT_TYPES:
        parts.append(slug(extraction.subject) or 'general')
    return '|'.join(parts)


def event_id(key: str) -> str:
    return 'evt_' + hashlib.sha1(key.encode()).hexdigest()[:16]


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r'[a-z0-9][a-z0-9.&%-]*', (text or '').lower()) if len(w) >= 3 and w not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def event_title(extraction: Extraction, headline: str) -> str:
    if extraction.metric and extraction.event_type.endswith(('_RELEASE', '_CLAIMS', '_DATA', '_PRODUCTION', '_GOODS')):
        country = extraction.countries[0] if extraction.countries else ''
        return f'{country} {extraction.metric} ({extraction.event_date})'.strip()
    return headline[:200]


def find_event(conn, extraction: Extraction, headline: str, published_at, vector: str | None, model: str | None,
               similarity_threshold: float = 0.86, lexical_threshold: float = 0.45) -> str | None:
    key = event_key(extraction)
    row = conn.execute('SELECT id FROM economic_events WHERE event_key = %s', (key,)).fetchone()
    if row:
        return row['id']
    window_start, window_end = published_at - timedelta(hours=48), published_at + timedelta(hours=48)
    if vector and model:
        row = conn.execute('''
            SELECT e.id, 1 - (k.embedding <=> %s::vector) AS similarity
            FROM knowledge_embeddings k JOIN economic_events e ON e.id = k.event_id
            WHERE k.kind = 'event' AND k.model = %s AND e.categories && %s
              AND e.last_seen_at >= %s AND e.first_seen_at <= %s
            ORDER BY k.embedding <=> %s::vector LIMIT 1''',
            (vector, model, list(extraction.categories), window_start, window_end, vector)).fetchone()
        if row and float(row['similarity']) >= similarity_threshold:
            return row['id']
    candidates = conn.execute('''
        SELECT id, title FROM economic_events
        WHERE event_type = %s AND countries && %s AND last_seen_at >= %s AND first_seen_at <= %s
        ORDER BY last_seen_at DESC LIMIT 25''',
        (extraction.event_type, [c.upper() for c in extraction.countries] or ['GLOBAL'],
         published_at - timedelta(hours=36), published_at + timedelta(hours=36))).fetchall()
    probe = tokens(headline) | tokens(extraction.fact_summary)
    best, best_score = None, 0.0
    for candidate in candidates:
        score = jaccard(probe, tokens(candidate['title']))
        if score > best_score:
            best, best_score = candidate['id'], score
    return best if best_score >= lexical_threshold else None
