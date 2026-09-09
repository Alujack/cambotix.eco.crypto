"""Daily macro brief: deterministic structure, optional model-written narrative, useful even before the first analysis."""
from datetime import date, datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import ai
from app.config import ASSET_UNIVERSE, ai_configured, ai_provider, model_for
from app.intel import LATEST_ANALYSIS, macro_current, upcoming_releases
from app.macro_state import DIMENSIONS, light

REGION_ORDER = ['US', 'EU', 'GLOBAL', 'CRYPTO']
TREND = {'RISING': '↑', 'FALLING': '↓', 'STABLE': '→'}
RULE = '━' * 26


def ai_model_label() -> str:
    return 'mock' if ai_provider() == 'mock' else f'{ai_provider()}:{model_for("analyst")}'


def build_daily(conn, brief_date: date | None = None, use_ai: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    brief_date = brief_date or now.date()
    current = macro_current(conn, now)
    since = now - timedelta(hours=24)
    developments = conn.execute(f'''
        SELECT e.id, e.title, e.importance, la.summary, la.key_risks, la.risk_regime_impact, la.relation_to_trend
        FROM economic_events e JOIN ({LATEST_ANALYSIS}) la ON la.event_id = e.id
        WHERE la.created_at >= %s ORDER BY e.importance DESC, la.created_at DESC LIMIT 8''', (since,)).fetchall()
    risks, seen = [], set()
    for row in developments:
        for risk in row['key_risks'] or []:
            key = risk.strip().lower()[:80]
            if key and key not in seen:
                seen.add(key)
                risks.append(risk.strip())
    stats = conn.execute('''
        SELECT count(*) FILTER (WHERE received_at >= %s) AS received_24h,
               count(DISTINCT source_key) FILTER (WHERE received_at >= %s) AS sources_24h,
               count(*) FILTER (WHERE status = 'queued') AS queued,
               count(*) FILTER (WHERE status = 'extracted' AND received_at >= %s) AS extracted_24h
        FROM raw_articles''', (since, since, since)).fetchone()
    # Official-source headlines carry information before any model has read them.
    official = conn.execute('''
        SELECT a.headline, a.published_at, s.name AS source FROM raw_articles a JOIN sources s ON s.key = a.source_key
        WHERE a.reliability >= 95 AND a.published_at >= %s AND a.source_key <> 'release_print'
        ORDER BY a.importance_prior DESC, a.published_at DESC LIMIT 6''', (since,)).fetchall()
    # The day's strongest headlines by keyword prior: the "news report" layer that exists before any model has read them.
    headlines = conn.execute('''
        SELECT a.headline, a.published_at, a.importance_prior, a.status,
               CASE WHEN s.key = 'alphavantage_news' THEN coalesce(a.publisher, s.name) ELSE s.name END AS source
        FROM raw_articles a JOIN sources s ON s.key = a.source_key
        WHERE a.published_at >= %s AND a.importance_prior >= 40 AND a.source_key <> 'release_print'
          AND NOT (a.reliability >= 95 AND a.headline = ANY(%s))
        ORDER BY a.importance_prior DESC, a.reliability DESC, a.published_at DESC LIMIT 8''',
        (since, [r['headline'] for r in official])).fetchall()
    flagged = conn.execute('''SELECT count(*) FILTER (WHERE jsonb_array_length(consistency_flags) > 0) AS flagged,
                                     count(*) AS total FROM event_analysis WHERE created_at >= %s''', (since,)).fetchone()
    ai_ready = ai_configured()
    brief = {
        'kind': 'daily', 'date': brief_date.isoformat(), 'generatedAt': now,
        'macroRegime': {region: {dim: {'state': v['state'], 'trend': v['trend'], 'score': v['score'], 'known': v['known']}
                                 for dim, v in dims.items()} for region, dims in current['regions'].items()},
        'riskRegime': current['riskRegime'], 'globalLiquidity': current['globalLiquidity'],
        'assetPressure': {asset: current['assets'][asset]['score'] for asset in ASSET_UNIVERSE},
        'developments': [{'eventId': r['id'], 'title': r['title'], 'importance': r['importance'], 'summary': r['summary'],
                          'riskRegimeImpact': r['risk_regime_impact'], 'relationToTrend': r['relation_to_trend']}
                         for r in developments],
        'upcoming': [{'currency': r['currency'], 'title': r['title'], 'impact': r['impact'], 'scheduledAt': r['scheduled_at'],
                      'forecast': r['forecast'], 'previous': r['previous']} for r in upcoming_releases(conn, 48, 'MEDIUM')],
        'officialHeadlines': [{'headline': r['headline'], 'source': r['source'], 'publishedAt': r['published_at']} for r in official],
        'topHeadlines': [{'headline': r['headline'], 'source': r['source'], 'publishedAt': r['published_at'],
                          'prior': r['importance_prior']} for r in headlines],
        'keyRisks': risks[:6],
        'pipeline': {'received24h': stats['received_24h'], 'sources24h': stats['sources_24h'], 'queued': stats['queued'],
                     'extracted24h': stats['extracted_24h'], 'analyzed24h': len(developments), 'aiConfigured': ai_ready,
                     'model': ai_model_label(), 'flagged24h': flagged['flagged'], 'analysesTotal24h': flagged['total']},
    }
    brief['narrative'] = ai.narrate_brief(brief) if use_ai and developments else None
    brief['text'] = render(brief)
    return brief


def render(brief: dict) -> str:
    generated = brief['generatedAt']
    stamp = f'{generated:%H:%M} UTC' if isinstance(generated, datetime) else str(generated)
    pipe = brief['pipeline']
    lines = [f"🌍 GLOBAL MACRO BRIEF — {brief['date']}  ({stamp})", '',
             f"Pipeline: {pipe['received24h']} items from {pipe['sources24h']} sources in 24h · {pipe['queued']} queued · "
             f"{pipe['analyzed24h']} events analyzed · analyst {pipe.get('model', '?')}"]
    if pipe.get('flagged24h'):
        lines.append(f"⚠ {pipe['flagged24h']}/{pipe['analysesTotal24h']} analyses have self-consistency flags "
                     f"(GET /analysis-quality) — treat their scores with caution.")
    if not pipe['aiConfigured']:
        lines.append('AI analysis is OFF — configure AI_PROVIDER (ollama / anthropic) to populate regime, developments and asset pressure.')
    lines += ['', 'MACRO REGIME', RULE]
    known_rows = [(region, dim, v) for region in REGION_ORDER for dim in DIMENSIONS.get(region, [])
                  for v in [brief['macroRegime'].get(region, {}).get(dim)] if v and v['known']]
    if known_rows:
        for region, dim, v in known_rows:
            lines.append(f"{light(dim, v['score'])} {region:<7}{dim.replace('_', ' '):<19}{v['state']:<20}{TREND.get(v['trend'], '')} {v['score']:+d}")
        unknown = sum(1 for region in REGION_ORDER for dim in DIMENSIONS.get(region, [])
                      if not brief['macroRegime'].get(region, {}).get(dim, {}).get('known'))
        if unknown:
            lines.append(f'⚪ {unknown} dimension(s) not yet informed by any analyzed event')
    else:
        lines.append('⚪ No macro state yet — the first analyzed events populate this section.')
    lines += [f"Risk regime: {brief['riskRegime']}   Global liquidity: {brief['globalLiquidity']}"]
    if brief.get('narrative'):
        lines += ['', brief['narrative']]
    lines += ['', 'HIGH IMPACT NEXT 48H', RULE]
    highs = [r for r in brief['upcoming'] if r['impact'] == 'HIGH']
    lines += [f"{_when(r['scheduledAt'])}  {r['currency']:<4}{r['title'][:34]:<35} fcst {r['forecast'] or '—'} · prev {r['previous'] or '—'}"
              for r in highs] or ['(none scheduled)']
    lines += ['', 'MAJOR DEVELOPMENTS', RULE]
    if brief['developments']:
        lines += [f"{i}. [{d['importance']}] {d['title']}\n   {d['summary']}" for i, d in enumerate(brief['developments'], 1)]
    elif not pipe['aiConfigured']:
        lines.append('(analysis pending — AI provider not configured)')
    else:
        lines.append('(no event reached the analysis threshold in the last 24h)')
    if brief.get('officialHeadlines'):
        lines += ['', 'OFFICIAL SOURCES (24H)', RULE]
        lines += [f"{_when(h['publishedAt'])}  {_clip(h['headline'], 70)}  — {h['source'].split(' — ')[0]}" for h in brief['officialHeadlines']]
    if brief.get('topHeadlines'):
        lines += ['', 'TOP HEADLINES (24H, by keyword signal)', RULE]
        lines += [f"{_when(h['publishedAt'])}  {_clip(h['headline'], 72)}  — {str(h['source']).split(' — ')[0][:24]}" for h in brief['topHeadlines']]
    lines += ['', 'ASSET MACRO PRESSURE', RULE]
    if any(brief['assetPressure'].values()):
        lines += [f"{asset:<8}{score:+d}" for asset, score in brief['assetPressure'].items()]
    else:
        lines.append('(no analyzed events yet — all assets neutral)')
    lines += ['', 'KEY RISKS', RULE]
    lines += [f'• {risk}' for risk in brief['keyRisks']] or ['• (none recorded)']
    return '\n'.join(lines)


def _clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _when(value) -> str:
    if isinstance(value, datetime):
        return f'{value:%a %H:%M}Z'
    return str(value)[:16]


def store(conn, brief: dict, model: str | None) -> None:
    conn.execute('''INSERT INTO briefs (kind, brief_date, macro_regime, asset_pressure, developments, upcoming, key_risks,
                                        narrative, text, model)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (kind, brief_date) DO UPDATE SET macro_regime = EXCLUDED.macro_regime,
                        asset_pressure = EXCLUDED.asset_pressure, developments = EXCLUDED.developments,
                        upcoming = EXCLUDED.upcoming, key_risks = EXCLUDED.key_risks, narrative = EXCLUDED.narrative,
                        text = EXCLUDED.text, model = EXCLUDED.model, created_at = now()''',
                 (brief['kind'], brief['date'], Jsonb(brief['macroRegime']), Jsonb(brief['assetPressure']),
                  Jsonb(brief['developments'], dumps=_dumps), Jsonb(brief['upcoming'], dumps=_dumps),
                  Jsonb(brief['keyRisks']), brief.get('narrative'), brief['text'], model))


def _dumps(value) -> str:
    import json
    return json.dumps(value, default=str)
