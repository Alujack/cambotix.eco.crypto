import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from app import ai, briefs, embeddings, intel, pipeline, sources
from app.config import ASSET_UNIVERSE, ai_provider, anthropic_configured, env, env_bool
from app.db import database
from app.schemas import IngestArticlesRequest, IngestCalendarRequest

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'), format='%(asctime)s %(name)s %(levelname)s %(message)s')
log = logging.getLogger('eco')


@asynccontextmanager
async def lifespan(_app):
    try:
        with database() as conn:
            count = sources.seed(conn)
        log.info('seeded %s sources; ai=%s (%s); embeddings=%s', count, ai_provider(),
                 'configured' if ai_provider() == 'mock' or anthropic_configured() else 'NOT CONFIGURED', embeddings.provider())
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
    return {'status': 'ok', 'ai': {'provider': ai_provider(),
                                   'configured': ai_provider() == 'mock' or anthropic_configured(),
                                   'extractModel': env('EXTRACT_MODEL', 'claude-haiku-4-5'),
                                   'analystModel': env('ANALYST_MODEL', 'claude-opus-5')},
            'embeddings': {'provider': embeddings.provider(), 'model': embeddings.model_name()}, 'trades': False}


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
    result = pipeline.run_extract(batch)
    if result.get('detail') == 'ai_not_configured':
        return JSONResponse(status_code=503, content=result)
    return result


@app.post('/process/analyze', dependencies=[Depends(authorize)])
def process_analyze(force: bool = False):
    result = pipeline.run_analyze(force)
    if result.get('error') == 'ai_not_configured':
        return JSONResponse(status_code=503, content=result)
    return result


@app.post('/process/reactions', dependencies=[Depends(authorize)])
def process_reactions():
    return pipeline.run_reactions()


@app.post('/briefs/daily', dependencies=[Depends(authorize)])
def make_daily_brief(brief_date: date | None = None):
    use_ai = env_bool('BRIEF_USE_AI', True) and (ai_provider() == 'mock' or anthropic_configured())
    with database() as conn:
        brief = briefs.build_daily(conn, brief_date, use_ai)
        briefs.store(conn, brief, pipeline._model_label() if use_ai else None)
    return brief


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
