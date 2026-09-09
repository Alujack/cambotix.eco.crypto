import hmac
import logging
import os
import threading
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from pathlib import Path

from app import ai, briefs, embeddings, intel, pipeline, sources, telegram
from app.config import (ASSET_UNIVERSE, ai_configured, ai_provider, brief_narrative_enabled, model_for,
                        ollama_base_url)
from app.db import database
from app.schemas import IngestArticlesRequest, IngestCalendarRequest

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger('eco')
SCHEMA = Path(__file__).resolve().parents[1] / 'db' / '02-schema.sql'
# Local inference is serial: a tick that arrives while a batch is running returns 'busy' instead of queueing.
EXTRACT_LOCK, ANALYZE_LOCK = threading.Lock(), threading.Lock()


@asynccontextmanager
async def lifespan(_app):
    try:
        with database() as conn:
            # The schema is written to be idempotent, so applying it on every start doubles as the migration step.
            conn.execute(SCHEMA.read_text())
            count = sources.seed(conn)
        log.info('seeded %s sources; ai=%s %s/%s (%s); embeddings=%s', count, ai_provider(), model_for('extract'),
                 model_for('analyst'), 'configured' if ai_configured() else 'NOT CONFIGURED', embeddings.provider())
    except psycopg.Error as error:
        log.warning('startup seed skipped, database not ready: %s', error)
    yield


app = FastAPI(title='Cambotix Economic Intelligence Engine', docs_url=None, redoc_url=None, lifespan=lifespan)


def authorize(x_eco_token: Annotated[str | None, Header()] = None):
    expected = os.environ.get('ENGINE_TOKEN', '')
    if not expected or not x_eco_token or not hmac.compare_digest(expected, x_eco_token):
        raise HTTPException(401, 'Unauthorized')


@app.exception_handler(psycopg.Error)
async def database_error(request, exc):
    log.error('database error: %s', exc)
    return JSONResponse(status_code=503, content={'detail': 'Database unavailable; retry later'})


@app.exception_handler(pipeline.UnknownSource)
async def unknown_source(request, exc):
    return JSONResponse(status_code=422, content={'detail': str(exc)})


@app.get('/health')
def health():
    with database() as conn:
        conn.execute('SELECT 1')
    ai_state = {'provider': ai_provider(), 'configured': ai_configured(), 'extractModel': model_for('extract'),
                'analystModel': model_for('analyst')}
    if ai_provider() == 'ollama':
        ai_state.update(ollama_status())
    return {'status': 'ok', 'ai': ai_state,
            'embeddings': {'provider': embeddings.provider(), 'model': embeddings.model_name()}, 'trades': False}


_OLLAMA_PROBE: dict = {'at': 0.0, 'value': {}}


def ollama_status(ttl: float = 30.0) -> dict:
    """Cached: the container healthcheck calls /health every 10s and this reaches out over the network."""
    import time

    import httpx
    if time.monotonic() - _OLLAMA_PROBE['at'] < ttl and _OLLAMA_PROBE['value']:
        return _OLLAMA_PROBE['value']
    try:
        with httpx.Client(timeout=3) as client:
            tags = client.get(ollama_base_url() + '/api/tags').json().get('models', [])
    except (httpx.HTTPError, ValueError) as error:
        value = {'ollama': ollama_base_url(), 'reachable': False, 'error': str(error)[:120]}
    else:
        names = {model['name'] for model in tags} | {model['name'].split(':')[0] for model in tags}
        wanted = {model_for('extract'), model_for('analyst'), embeddings.model_name() or ''} - {''}
        missing = sorted(m for m in wanted if m not in names and m.split(':')[0] not in names)
        value = {'ollama': ollama_base_url(), 'reachable': True, 'missingModels': missing}
    _OLLAMA_PROBE.update(at=time.monotonic(), value=value)
    return value


# ---- write path (called by n8n) ---------------------------------------------------------------------------------
@app.post('/ingest/articles', dependencies=[Depends(authorize)])
def ingest_articles(request: IngestArticlesRequest):
    with database() as conn:
        return pipeline.ingest_articles(conn, request)


@app.post('/ingest/calendar', dependencies=[Depends(authorize)])
def ingest_calendar(request: IngestCalendarRequest):
    with database() as conn:
        return pipeline.ingest_calendar(conn, request)


@app.post('/process/extract', dependencies=[Depends(authorize)])
def process_extract(batch: int | None = Query(default=None, ge=1, le=50)):
    if not EXTRACT_LOCK.acquire(blocking=False):
        return {'skipped': 'busy', 'detail': 'a previous extract batch is still running'}
    try:
        result = pipeline.run_extract(batch)
    finally:
        EXTRACT_LOCK.release()
    if result.get('detail') == 'ai_not_configured':
        return JSONResponse(status_code=503, content=result)
    return result


@app.post('/process/analyze', dependencies=[Depends(authorize)])
def process_analyze(force: bool = False):
    if not ANALYZE_LOCK.acquire(blocking=False):
        return {'analyzed': 0, 'skipped': 'busy', 'detail': 'a previous analysis is still running'}
    try:
        result = pipeline.run_analyze(force)
    finally:
        ANALYZE_LOCK.release()
    if result.get('error') == 'ai_not_configured':
        return JSONResponse(status_code=503, content=result)
    return result


@app.post('/process/reactions', dependencies=[Depends(authorize)])
def process_reactions():
    return pipeline.run_reactions()


@app.post('/briefs/daily', dependencies=[Depends(authorize)])
def make_daily_brief(brief_date: date | None = None, notify: bool = True):
    use_ai = brief_narrative_enabled() and ai_configured()
    with database() as conn:
        brief = briefs.build_daily(conn, brief_date, use_ai)
        briefs.store(conn, brief, pipeline._model_label() if use_ai else None)
        queued = telegram.enqueue(conn, 'brief', f"daily:{brief['date']}", brief['text'], 'HTML_PRE') if notify else None
    brief['telegram'] = telegram.deliver_pending() if queued else {'skipped': 'not configured' if notify else 'notify=false'}
    return brief


# ---- notifications (Telegram outbox) -----------------------------------------------------------------------------
@app.post('/notify/flush', dependencies=[Depends(authorize)])
def notify_flush():
    if not telegram.configured():
        return {'skipped': 'TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set'}
    return telegram.deliver_pending()


@app.post('/notify/test', dependencies=[Depends(authorize)])
def notify_test():
    if not telegram.configured():
        raise HTTPException(503, 'TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set')
    stamp = f'{__import__("datetime").datetime.now(__import__("datetime").timezone.utc):%Y-%m-%d %H:%M:%S} UTC'
    with database() as conn:
        telegram.enqueue(conn, 'test', stamp, '✅ Cambotix Economic Intelligence Engine connected.\n'
                         'Daily macro brief arrives at 06:00 UTC; high-impact event alerts as they are analyzed.\n' + stamp)
    return telegram.deliver_pending()


@app.get('/analysis-quality', dependencies=[Depends(authorize)])
def analysis_quality(days: int = Query(default=7, ge=1, le=90)):
    """How often the analyst contradicts itself, by model. Use it to decide whether a local model is good enough."""
    with database() as conn:
        # Aggregate per analysis first: a lateral join over the flag array would multiply the row count.
        return conn.execute('''
            SELECT model, count(*) AS analyses,
                   count(*) FILTER (WHERE flag_count > 0) AS flagged,
                   coalesce(sum(flag_count), 0) AS flags,
                   round(avg(confidence)) AS avg_confidence,
                   round(avg(flag_count), 2) AS avg_flags_per_analysis,
                   (SELECT jsonb_agg(DISTINCT code) FROM (
                        SELECT f->>'code' AS code FROM event_analysis inner_ea
                        CROSS JOIN LATERAL jsonb_array_elements(inner_ea.consistency_flags) f
                        WHERE inner_ea.model = ea.model AND inner_ea.created_at >= now() - make_interval(days => %s)
                    ) c) AS codes
            FROM (SELECT model, confidence, created_at, jsonb_array_length(consistency_flags) AS flag_count
                  FROM event_analysis WHERE created_at >= now() - make_interval(days => %s)) ea
            GROUP BY model ORDER BY analyses DESC''', (days, days)).fetchall()


@app.get('/notify/status', dependencies=[Depends(authorize)])
def notify_status():
    with database() as conn:
        return telegram.status(conn)


# ---- intelligence API (consumed by the gold / forex / crypto engines later) --------------------------------------
@app.get('/macro/current', dependencies=[Depends(authorize)])
def macro_current():
    with database() as conn:
        return intel.macro_current(conn)


@app.get('/macro/history', dependencies=[Depends(authorize)])
def macro_history(region: str | None = None, dimension: str | None = None, limit: int = Query(default=100, le=1000)):
    with database() as conn:
        return conn.execute('''SELECT * FROM macro_state_history WHERE (%s::text IS NULL OR region = %s)
                               AND (%s::text IS NULL OR dimension = %s) ORDER BY created_at DESC LIMIT %s''',
                            (region, region, dimension, dimension, limit)).fetchall()


@app.get('/events/recent', dependencies=[Depends(authorize)])
def events_recent(hours: int = Query(default=24, ge=1, le=720), min_importance: int = Query(default=0, ge=0, le=100),
                  limit: int = Query(default=50, ge=1, le=500)):
    with database() as conn:
        return intel.recent_events(conn, hours, min_importance, limit)


@app.get('/events/upcoming', dependencies=[Depends(authorize)])
def events_upcoming(hours: int = Query(default=48, ge=1, le=336), min_impact: str = 'MEDIUM'):
    with database() as conn:
        return intel.upcoming_releases(conn, hours, min_impact)


@app.get('/events/{event_id}', dependencies=[Depends(authorize)])
def event_detail(event_id: str):
    with database() as conn:
        detail = intel.event_detail(conn, event_id)
    if detail is None:
        raise HTTPException(404, 'Unknown event')
    return detail


@app.get('/analysis/{asset}', dependencies=[Depends(authorize)])
def analysis_for_asset(asset: str, days: int = Query(default=14, ge=1, le=90)):
    asset = asset.upper()
    if asset not in ASSET_UNIVERSE:
        raise HTTPException(404, f'Unknown asset; one of {", ".join(ASSET_UNIVERSE)}')
    with database() as conn:
        return intel.analysis_for_asset(conn, asset, days)


@app.get('/briefs/latest', dependencies=[Depends(authorize)])
def latest_brief(kind: str = 'daily', format: str = 'json'):
    with database() as conn:
        row = conn.execute('SELECT * FROM briefs WHERE kind = %s ORDER BY brief_date DESC LIMIT 1', (kind,)).fetchone()
    if row is None:
        raise HTTPException(404, 'No brief yet')
    if format == 'text':
        return PlainTextResponse(row['text'])
    return row


@app.get('/sources', dependencies=[Depends(authorize)])
def list_sources():
    with database() as conn:
        return conn.execute('''SELECT s.key, s.name, s.category, s.country, s.type, s.priority, s.reliability, s.enabled,
                                      count(a.id) AS articles, max(a.received_at) AS last_received
                               FROM sources s LEFT JOIN raw_articles a ON a.source_key = s.key
                               GROUP BY s.key ORDER BY s.priority DESC, s.key''').fetchall()
