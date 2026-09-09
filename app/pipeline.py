"""The write path: ingest -> extract -> cluster -> analyze -> macro state -> reactions. Each step is a small,
idempotent unit that n8n triggers on a schedule."""
import logging
import re
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import ai, clustering, embeddings, macro_state, reactions, telegram
from app.config import MEDIA_SOURCE_CATEGORIES, env_int
from app.db import database
from app.normalize import article_id, classify, clean_text, content_hash, parse_datetime
from app.schemas import Extraction, IngestArticlesRequest, IngestCalendarRequest
from app.sources import publisher_reliability

log = logging.getLogger('eco.pipeline')
REACTION_TYPES = {'MARKET_COMMENTARY'}
IMPACT_MAP = {'high': 'HIGH', 'medium': 'MEDIUM', 'low': 'LOW'}
_NUMBER = re.compile(r'-?\d+(?:\.\d+)?')


class UnknownSource(ValueError):
    pass


# ---- ingest -----------------------------------------------------------------------------------------------------
def ingest_articles(conn, request: IngestArticlesRequest) -> dict:
    sources = {row['key']: row for row in conn.execute('SELECT * FROM sources WHERE enabled').fetchall()}
    counts = {'received': len(request.items), 'inserted': 0, 'duplicates': 0, 'stale': 0}
    now = datetime.now(timezone.utc)
    max_age = timedelta(hours=env_int('INGEST_MAX_AGE_HOURS', 72))
    for item in request.items:
        key = item.source or request.source
        if key not in sources:
            raise UnknownSource(f'unknown source {key!r}; register it in sources/registry.json')
        source = sources[key]
        headline = clean_text(item.headline, 500)
        content = clean_text(item.content, 8000)
        published = parse_datetime(item.publishedAt) or now
        if published > now + timedelta(minutes=10):
            published = now
        digest = content_hash(key, item.externalId, item.url, headline)
        reliability = publisher_reliability(item.publisher, source['reliability']) if key == 'alphavantage_news' \
            else source['reliability']
        tags = classify(headline, content, source, item.topics)
        stale = now - published > max_age
        inserted = conn.execute('''
            INSERT INTO raw_articles (id, content_hash, source_key, publisher, reliability, published_at, headline, content,
                                      url, countries, categories, entities, assets, importance_prior, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (content_hash) DO NOTHING RETURNING id''',
            (article_id(digest), digest, key, item.publisher, reliability, published, headline, content or None,
             item.url, tags['countries'], tags['categories'], tags['entities'], tags['assets'], tags['importance_prior'],
             'ignored' if stale else 'queued')).fetchone()
        if inserted is None:
            counts['duplicates'] += 1
        elif stale:
            counts['stale'] += 1
        else:
            counts['inserted'] += 1
    return counts


def ingest_calendar(conn, request: IngestCalendarRequest) -> dict:
    source = conn.execute('SELECT * FROM sources WHERE key = %s AND enabled', (request.source,)).fetchone()
    if source is None:
        raise UnknownSource(f'unknown calendar source {request.source!r}')
    counts = {'received': len(request.items), 'upserted': 0, 'skipped': 0, 'prints': 0}
    for item in request.items:
        impact = IMPACT_MAP.get(str(item.impact).strip().lower())
        scheduled = parse_datetime(item.scheduledAt)
        if impact is None or scheduled is None:
            counts['skipped'] += 1
            continue
        currency = item.currency.strip().upper()
        release_id = 'rel_' + content_hash(request.source, None, None, f'{currency}|{item.title}|{scheduled.isoformat()}')[:20]
        previous_row = conn.execute('SELECT actual FROM economic_releases WHERE id = %s', (release_id,)).fetchone()
        actual = (item.actual or '').strip() or None
        conn.execute('''INSERT INTO economic_releases (id, source_key, currency, title, impact, scheduled_at, actual, forecast,
                                                       previous, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                        ON CONFLICT (currency, title, scheduled_at) DO UPDATE SET impact = EXCLUDED.impact,
                            actual = COALESCE(EXCLUDED.actual, economic_releases.actual),
                            forecast = COALESCE(EXCLUDED.forecast, economic_releases.forecast),
                            previous = COALESCE(EXCLUDED.previous, economic_releases.previous), updated_at = now()''',
                     (release_id, request.source, currency, item.title.strip(), impact, scheduled, actual,
                      (item.forecast or '').strip() or None, (item.previous or '').strip() or None))
        counts['upserted'] += 1
        first_print = actual and (previous_row is None or previous_row['actual'] is None) and impact == 'HIGH'
        if first_print and _synthesize_print(conn, currency, item, scheduled, actual):
            counts['prints'] += 1
    return counts


def _synthesize_print(conn, currency: str, item, scheduled: datetime, actual: str) -> bool:
    """A HIGH-impact print becomes an article so actual-vs-forecast reaches the analyst without waiting for coverage."""
    source = conn.execute('SELECT * FROM sources WHERE key = %s', ('release_print',)).fetchone()
    if source is None:
        return False
    headline = f'{currency} {item.title.strip()}: actual {actual} vs {item.forecast or "n/a"} forecast (previous {item.previous or "n/a"})'
    digest = content_hash('release_print', None, None, f'{currency}|{item.title}|{scheduled.isoformat()}|{actual}')
    tags = classify(headline, '', source)
    tags['importance_prior'] = max(tags['importance_prior'], 80)
    if currency not in tags['countries']:
        tags['countries'] = [{'USD': 'US', 'EUR': 'EU', 'GBP': 'GB', 'JPY': 'JP', 'CNY': 'CN'}.get(currency, currency)]
    inserted = conn.execute('''
        INSERT INTO raw_articles (id, content_hash, source_key, reliability, published_at, headline, content, countries,
                                  categories, entities, assets, importance_prior, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued') ON CONFLICT (content_hash) DO NOTHING RETURNING id''',
        (article_id(digest), digest, 'release_print', source['reliability'], max(scheduled, datetime.now(timezone.utc) - timedelta(minutes=1)),
         headline, f'Scheduled release {item.title.strip()} for {currency} at {scheduled.isoformat()}. Actual {actual}, '
                   f'forecast {item.forecast or "n/a"}, previous {item.previous or "n/a"}.',
         tags['countries'], tags['categories'], tags['entities'], tags['assets'], tags['importance_prior'])).fetchone()
    return inserted is not None


# ---- stage 1: extract + cluster ---------------------------------------------------------------------------------
def claim_articles(batch: int) -> list[dict]:
    with database() as conn:
        rows = conn.execute('''
            SELECT a.*, s.name AS source_name, s.category AS source_category FROM raw_articles a
            JOIN sources s ON s.key = a.source_key
            WHERE a.status = 'queued' AND a.next_attempt_at <= now()
            ORDER BY a.importance_prior DESC, a.received_at LIMIT %s FOR UPDATE OF a SKIP LOCKED''', (batch,)).fetchall()
        if rows:
            conn.execute("UPDATE raw_articles SET status = 'extracting', attempts = attempts + 1 WHERE id = ANY(%s)",
                         ([row['id'] for row in rows],))
        return rows


def run_extract(batch: int | None = None) -> dict:
    batch = batch or env_int('EXTRACT_BATCH', 5)
    min_prior = env_int('EXTRACT_MIN_PRIOR', 15)
    counts = {'claimed': 0, 'extracted': 0, 'ignored': 0, 'errors': 0, 'events_created': 0, 'events_linked': 0}
    rows = claim_articles(batch)
    counts['claimed'] = len(rows)
    for row in rows:
        if row['importance_prior'] < min_prior and row['source_category'] in MEDIA_SOURCE_CATEGORIES:
            _finish_article(row['id'], 'ignored', None)
            counts['ignored'] += 1
            continue
        try:
            extraction = ai.extract(row)
        except ai.AINotConfigured:
            _requeue([r['id'] for r in rows], seconds=60, error='ai_not_configured')
            counts['errors'] += 1
            counts['detail'] = 'ai_not_configured'
            return counts
        except (ai.AIError, ValueError) as error:
            log.warning('extract %s failed: %s', row['id'], error)
            _fail_article(row, str(error)[:120])
            counts['errors'] += 1
            continue
        if not extraction.is_relevant:
            _finish_article(row['id'], 'ignored', extraction)
            counts['ignored'] += 1
            continue
        vector = None
        if embeddings.enabled():
            vectors = embeddings.embed([f"{row['headline']}\n{extraction.fact_summary}"])
            vector = embeddings.to_pgvector(vectors[0]) if vectors else None
        created = attach_to_event(row, extraction, vector)
        counts['extracted'] += 1
        counts['events_created' if created else 'events_linked'] += 1
    return counts


def _requeue(ids: list[str], seconds: int, error: str) -> None:
    with database() as conn:
        conn.execute('''UPDATE raw_articles SET status = 'queued', attempts = GREATEST(attempts - 1, 0), error_code = %s,
                        next_attempt_at = now() + make_interval(secs => %s) WHERE id = ANY(%s)''', (error, seconds, ids))


def _fail_article(row: dict, error: str) -> None:
    with database() as conn:
        if row['attempts'] >= 3:
            conn.execute("UPDATE raw_articles SET status = 'error', error_code = %s WHERE id = %s", (error, row['id']))
        else:
            conn.execute('''UPDATE raw_articles SET status = 'queued', error_code = %s,
                            next_attempt_at = now() + make_interval(secs => %s) WHERE id = %s''',
                         (error, 60 * row['attempts'] ** 2, row['id']))


def _finish_article(article_id_: str, status: str, extraction: Extraction | None) -> None:
    with database() as conn:
        conn.execute('UPDATE raw_articles SET status = %s, extraction = %s, error_code = NULL WHERE id = %s',
                     (status, Jsonb(extraction.model_dump()) if extraction else None, article_id_))


def article_importance(extraction: Extraction, prior: int, reliability: int) -> int:
    blended = 0.6 * extraction.importance + 0.4 * prior
    return int(round(max(0, min(100, blended * (0.6 + 0.4 * reliability / 100)))))


def attach_to_event(row: dict, extraction: Extraction, vector: str | None) -> bool:
    """Link the article to an existing event or create one; merge facts; flag the event for (re-)analysis."""
    importance = article_importance(extraction, row['importance_prior'], row['reliability'])
    role = 'reaction' if extraction.is_market_reaction_coverage or extraction.event_type in REACTION_TYPES else 'supporting'
    created = False
    with database() as conn:
        event_id = clustering.find_event(conn, extraction, row['headline'], row['published_at'], vector, embeddings.model_name())
        if event_id is None:
            key = clustering.event_key(extraction)
            event_id = clustering.event_id(key)
            conn.execute('''INSERT INTO economic_events (id, event_key, event_type, title, subject, event_date, countries,
                                                         categories, importance, primary_source_key, primary_article_id,
                                                         first_seen_at, last_seen_at, facts, affected_assets, needs_analysis)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
                            ON CONFLICT (event_key) DO NOTHING''',
                         (event_id, key, extraction.event_type, clustering.event_title(extraction, row['headline']),
                          extraction.subject[:200], _safe_date(extraction.event_date, row['published_at']),
                          [c.upper() for c in extraction.countries] or ['GLOBAL'], list(extraction.categories), importance,
                          row['source_key'], row['id'], row['published_at'], row['published_at'],
                          Jsonb(_facts_from(extraction, row)), list(extraction.assets)))
            created = True
            role = 'primary' if role != 'reaction' else role
        event = conn.execute('SELECT * FROM economic_events WHERE id = %s FOR UPDATE', (event_id,)).fetchone()
        conn.execute('''INSERT INTO event_articles (event_id, article_id, role) VALUES (%s, %s, %s)
                        ON CONFLICT DO NOTHING''', (event_id, row['id'], role))
        facts = dict(event['facts'] or {})
        primary_article_id, primary_source = event['primary_article_id'], event['primary_source_key']
        if role != 'reaction' and row['reliability'] >= int(facts.get('reliability') or 0):
            facts.update(_facts_from(extraction, row))
            primary_article_id, primary_source = row['id'], row['source_key']
        elif not created and 'reaction_notes' in facts or role == 'reaction':
            notes = list(facts.get('reaction_notes') or [])[-3:]
            notes.append(extraction.fact_summary[:200])
            facts['reaction_notes'] = notes
        new_importance = max(event['importance'], importance)
        informative = role != 'reaction'
        conn.execute('''UPDATE economic_events SET article_count = article_count + 1, last_seen_at = GREATEST(last_seen_at, %s),
                            importance = %s, facts = %s, affected_assets = ARRAY(SELECT DISTINCT unnest(affected_assets || %s)),
                            countries = ARRAY(SELECT DISTINCT unnest(countries || %s)),
                            categories = ARRAY(SELECT DISTINCT unnest(categories || %s)),
                            primary_article_id = %s, primary_source_key = %s,
                            needs_analysis = needs_analysis OR %s, status = CASE WHEN %s THEN 'open' ELSE status END
                        WHERE id = %s''',
                     (row['published_at'], new_importance, Jsonb(facts), list(extraction.assets),
                      [c.upper() for c in extraction.countries], list(extraction.categories), primary_article_id,
                      primary_source, informative, informative and event['status'] == 'analyzed', event_id))
        conn.execute('''UPDATE raw_articles SET status = 'extracted', extraction = %s, event_id = %s, error_code = NULL
                        WHERE id = %s''', (Jsonb(extraction.model_dump()), event_id, row['id']))
        if vector:
            embeddings.store(conn, 'article', row['id'], f"{row['headline']}\n{extraction.fact_summary}", vector, event_id)
            if created:
                embeddings.store(conn, 'event', event_id, f"{event['title']}\n{extraction.fact_summary}", vector, event_id)
    return created


def _safe_date(value: str, fallback: datetime):
    try:
        return datetime.strptime(value[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return fallback.date()


def _facts_from(extraction: Extraction, row: dict) -> dict:
    return {'fact_summary': extraction.fact_summary, 'metric': extraction.metric, 'actual': extraction.actual,
            'forecast': extraction.forecast, 'previous': extraction.previous, 'unit': extraction.unit,
            'surprise': extraction.surprise, 'tone': extraction.tone, 'institutions': extraction.institutions,
            'people': extraction.people, 'reliability': row['reliability'], 'source': row['source_key']}


# ---- stage 2: analyze -------------------------------------------------------------------------------------------
def claim_event(force: bool = False) -> dict | None:
    """force skips the coverage debounce and the re-analysis gap (manual runs, smoke test); thresholds still apply."""
    min_importance = env_int('ANALYZE_MIN_IMPORTANCE', 40)
    debounce = env_int('ANALYZE_DEBOUNCE_SECONDS', 180)
    reanalyze = env_int('REANALYZE_MIN_SECONDS', 900)
    with database() as conn:
        row = conn.execute('''
            SELECT * FROM economic_events
            WHERE needs_analysis AND importance >= %s AND next_attempt_at <= now() AND status <> 'analyzing'
              AND last_seen_at >= now() - interval '5 days'
              AND (%s OR importance >= 80 OR last_seen_at <= now() - make_interval(secs => %s))
              AND (%s OR analyzed_at IS NULL OR analyzed_at <= now() - make_interval(secs => %s))
            ORDER BY importance DESC, last_seen_at LIMIT 1 FOR UPDATE SKIP LOCKED''',
            (min_importance, force, debounce, force, reanalyze)).fetchone()
        if row:
            conn.execute("UPDATE economic_events SET status = 'analyzing', attempts = attempts + 1 WHERE id = %s", (row['id'],))
        return row


def build_context(conn, event: dict) -> dict:
    articles = conn.execute('''
        SELECT a.headline, a.content, a.source_key, a.publisher, a.reliability, a.published_at, a.url, ea.role,
               a.extraction->>'fact_summary' AS fact_summary
        FROM event_articles ea JOIN raw_articles a ON a.id = ea.article_id WHERE ea.event_id = %s
        ORDER BY (ea.role = 'reaction'), a.reliability DESC, a.published_at LIMIT 12''', (event['id'],)).fetchall()
    regions = {'GLOBAL'} | {c for c in event['countries'] if c in ('US', 'EU')}
    if 'CRYPTO' in event['categories']:
        regions.add('CRYPTO')
    state = conn.execute('SELECT region, dimension, score, state, trend, confidence FROM macro_state WHERE region = ANY(%s)',
                         (list(regions),)).fetchall()
    previous = conn.execute('SELECT version, summary, confidence, created_at FROM event_analysis WHERE event_id = %s '
                            'ORDER BY version DESC LIMIT 1', (event['id'],)).fetchone()
    same_type = conn.execute(f'''
        SELECT e.title, e.event_date, e.facts, la.summary FROM economic_events e
        LEFT JOIN (SELECT DISTINCT ON (event_id) * FROM event_analysis ORDER BY event_id, version DESC) la ON la.event_id = e.id
        WHERE e.event_type = %s AND e.id <> %s AND e.countries && %s ORDER BY e.event_date DESC NULLS LAST LIMIT 3''',
        (event['event_type'], event['id'], event['countries'])).fetchall()
    related = []
    if embeddings.enabled():
        vectors = embeddings.embed([f"{event['title']}\n{(event['facts'] or {}).get('fact_summary', '')}"])
        if vectors:
            related = embeddings.similar(conn, ['event', 'analysis'], embeddings.to_pgvector(vectors[0]), 6, event['id'])
    if not related:
        related = conn.execute(f'''
            SELECT e.title, la.summary AS text, la.created_at FROM economic_events e
            JOIN (SELECT DISTINCT ON (event_id) * FROM event_analysis ORDER BY event_id, version DESC) la ON la.event_id = e.id
            WHERE e.id <> %s AND e.categories && %s AND la.created_at >= now() - interval '30 days'
            ORDER BY e.importance DESC, la.created_at DESC LIMIT 6''', (event['id'], event['categories'])).fetchall()
    priors = conn.execute('SELECT driver, asset, direction, note FROM economic_asset_map ORDER BY driver, asset').fetchall()
    releases = conn.execute('''SELECT currency, title, scheduled_at, actual, forecast, previous FROM economic_releases
                               WHERE scheduled_at::date = %s AND actual IS NOT NULL ORDER BY scheduled_at LIMIT 10''',
                            (event['event_date'],)).fetchall()
    return {
        'now': datetime.now(timezone.utc),
        'event': {'id': event['id'], 'title': event['title'], 'type': event['event_type'], 'date': event['event_date'],
                  'countries': event['countries'], 'categories': event['categories'], 'importance': event['importance'],
                  'facts': event['facts'], 'article_count': event['article_count'],
                  'primary_source': event['primary_source_key']},
        'coverage': [{'role': a['role'], 'source': a['publisher'] or a['source_key'], 'reliability': a['reliability'],
                      'published_at': a['published_at'], 'headline': a['headline'], 'facts': a['fact_summary'],
                      'excerpt': (a['content'] or '')[:1200]} for a in articles],
        'current_macro_state': state,
        'previous_analysis_of_this_event': previous,
        'prior_events_same_type': same_type,
        'related_knowledge': [{'text': r.get('text'), 'title': r.get('title'), 'at': r.get('created_at'),
                               'similarity': r.get('similarity')} for r in related],
        'same_day_release_prints': releases,
        'economic_asset_map': priors,
    }


def run_analyze(force: bool = False) -> dict:
    event = claim_event(force)
    if event is None:
        return {'analyzed': 0, 'reason': 'nothing due'}
    try:
        with database() as conn:
            context = build_context(conn, event)
        analysis = ai.analyze(context)
    except ai.AINotConfigured:
        _release_event(event, seconds=60, error='ai_not_configured')
        return {'analyzed': 0, 'eventId': event['id'], 'error': 'ai_not_configured'}
    except (ai.AIError, ValueError) as error:
        log.warning('analyze %s failed: %s', event['id'], error)
        _release_event(event, seconds=120 * max(1, event['attempts']) ** 2, error=str(error)[:120])
        return {'analyzed': 0, 'eventId': event['id'], 'error': str(error)[:200]}
    reliability = int((event['facts'] or {}).get('reliability') or 60)
    prices = reactions.fetch_prices(reactions.measurable([i.asset for i in analysis.asset_impacts])) if analysis.asset_impacts else {}
    with database() as conn:
        version = event['analysis_version'] + 1
        analysis_id = conn.execute('''
            INSERT INTO event_analysis (event_id, version, model, summary, what_happened, why_it_matters, what_changed,
                economic_interpretation, central_bank_implication, risk_regime_impact, causal_chain, relation_to_trend,
                horizon, is_new_information, evidence_strength, confidence, key_risks, raw)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id''',
            (event['id'], version, _model_label(), analysis.summary, analysis.what_happened, analysis.why_it_matters,
             analysis.what_changed_vs_expectations, Jsonb(analysis.economic_interpretation.model_dump()),
             Jsonb(analysis.central_bank_implication.model_dump()), analysis.risk_regime_impact, Jsonb(analysis.causal_chain),
             analysis.relation_to_trend, analysis.horizon, analysis.is_new_information, analysis.evidence_strength,
             analysis.confidence, Jsonb(analysis.key_risks), Jsonb(analysis.model_dump()))).fetchone()['id']
        for impact in analysis.asset_impacts:
            conn.execute('''INSERT INTO asset_impacts (analysis_id, event_id, asset, immediate_direction, immediate_score,
                                short_term_direction, short_term_score, medium_term_direction, medium_term_score, rationale)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                         (analysis_id, event['id'], impact.asset, impact.immediate.direction, impact.immediate.score,
                          impact.short_term.direction, impact.short_term.score, impact.medium_term.direction,
                          impact.medium_term.score, impact.rationale))
        changes = macro_state.apply_updates(conn, event['id'], event['importance'], analysis, reliability)
        anchor = datetime.now(timezone.utc)
        opened = reactions.open_windows(conn, event['id'], analysis.asset_impacts, anchor, prices) if version == 1 else 0
        conn.execute('''UPDATE economic_events SET status = 'analyzed', needs_analysis = false, analysis_version = %s,
                        analyzed_at = now(), affected_assets = %s, error_code = NULL, next_attempt_at = now() WHERE id = %s''',
                     (version, [i.asset for i in analysis.asset_impacts] or event['affected_assets'], event['id']))
        if embeddings.enabled():
            vectors = embeddings.embed([f"{event['title']}\n{analysis.summary}"])
            if vectors:
                vector = embeddings.to_pgvector(vectors[0])
                embeddings.store(conn, 'event', event['id'], f"{event['title']}\n{analysis.summary}", vector, event['id'])
                embeddings.store(conn, 'analysis', str(analysis_id), analysis.summary, vector, event['id'])
    alert = None
    if version == 1 and telegram.configured() and event['importance'] >= env_int('TELEGRAM_ALERT_MIN_IMPORTANCE', 80):
        try:
            with database() as conn:
                telegram.enqueue(conn, 'event_alert', event['id'],
                                 telegram.format_event_alert(event, analysis, event['article_count']), 'HTML')
            alert = telegram.deliver_pending()
        except Exception as error:  # delivery must never undo a stored analysis
            log.warning('alert for %s not delivered: %s', event['id'], error)
            alert = {'error': str(error)[:200]}
    return {'analyzed': 1, 'eventId': event['id'], 'version': version, 'summary': analysis.summary,
            'assetImpacts': len(analysis.asset_impacts), 'macroStateChanges': changes, 'reactionWindows': opened,
            'alert': alert}


def _model_label() -> str:
    from app.config import ai_provider, env
    return 'mock' if ai_provider() == 'mock' else (env('ANALYST_MODEL', 'claude-opus-5') or 'claude-opus-5')


def _release_event(event: dict, seconds: int, error: str) -> None:
    with database() as conn:
        if event['attempts'] >= 5 and error != 'ai_not_configured':
            conn.execute("UPDATE economic_events SET status = 'error', needs_analysis = false, error_code = %s WHERE id = %s",
                         (error, event['id']))
        else:
            conn.execute('''UPDATE economic_events SET status = 'open', error_code = %s,
                            attempts = CASE WHEN %s THEN GREATEST(attempts - 1, 0) ELSE attempts END,
                            next_attempt_at = now() + make_interval(secs => %s) WHERE id = %s''',
                         (error, error == 'ai_not_configured', seconds, event['id']))


def run_reactions() -> dict:
    with database() as conn:
        measured = reactions.measure_due(conn)
        pending = conn.execute('SELECT count(*) AS n FROM market_reactions WHERE measured_at IS NULL').fetchone()['n']
    return {'measured': measured, 'pending': pending}
