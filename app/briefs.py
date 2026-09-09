"""Daily macro brief: deterministic structure, optional model-written narrative, useful even before the first analysis."""
import logging
from datetime import date, datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import ai, grounding, i18n
from app.config import ASSET_UNIVERSE, ai_configured, ai_provider, model_for, output_language
from app.intel import LATEST_ANALYSIS, macro_current, upcoming_releases
from app.macro_state import DIMENSIONS, light

log = logging.getLogger('eco.briefs')
REGION_ORDER = ['US', 'EU', 'GLOBAL', 'CRYPTO']
TREND = {'RISING': '↑', 'FALLING': '↓', 'STABLE': '→'}
RULE = '━' * 26


def ai_model_label() -> str:
    from app.config import decompose_analysis
    if ai_provider() == 'mock':
        return 'mock'
    return f'{ai_provider()}:{model_for("analyst")}' + ('+decomposed' if decompose_analysis() else '')


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
    brief['narrative'], brief['narrativeWithheld'] = _narrative(brief, use_ai and bool(developments))
    brief['text'] = render(brief)
    # Stored and served in English (the trading engines read this); `textLocalized` is what Telegram delivers.
    lang = output_language()
    if lang != 'en':
        brief['textLocalized'] = render(_localized(brief, lang), lang)
    return brief


def _localized(brief: dict, lang: str) -> dict:
    """A copy of the brief whose prose is in `lang`. Headlines, event titles and source names are verbatim source
    text, so they stay as published; the labels come from app.i18n at render time."""
    prose = ([brief['narrative']] if brief.get('narrative') else []) + list(brief['keyRisks'])
    prose += [d['summary'] for d in brief['developments'] if d.get('summary')]
    delivered = dict(zip(prose, i18n.translate(prose, lang)))
    return {**brief,
            'narrative': delivered.get(brief.get('narrative'), brief.get('narrative')),
            'keyRisks': [delivered.get(risk, risk) for risk in brief['keyRisks']],
            'developments': [{**d, 'summary': delivered.get(d.get('summary'), d.get('summary'))}
                             for d in brief['developments']]}


def _narrative(brief: dict, use_ai: bool) -> tuple[str | None, list[str] | None]:
    """Publish model prose only if its specific claims appear in the data it was given."""
    if not use_ai:
        return None, None
    text = ai.narrate_brief(brief)
    if not text:
        return None, None
    ungrounded = grounding.ungrounded_claims(text, brief)
    if ungrounded:
        log.warning('narrative withheld: %s not found in the brief data', ', '.join(ungrounded[:8]))
        return None, ungrounded
    return text, None


def render(brief: dict, lang: str = 'en') -> str:
    """The brief as delivered text. English keeps the fixed-width column layout (Telegram renders it in a <pre>
    block); other languages are laid out with separators, because no proportional script sits on a character grid."""
    L = i18n.labels(lang)
    generated = brief['generatedAt']
    stamp = f'{generated:%H:%M} UTC' if isinstance(generated, datetime) else str(generated)
    pipe = brief['pipeline']
    lines = [f"{L['brief_title']} — {brief['date']}  ({stamp})", '',
             L['pipeline_line'].format(received=pipe['received24h'], sources=pipe['sources24h'], queued=pipe['queued'],
                                       analyzed=pipe['analyzed24h'], model=pipe.get('model', '?'))]
    if pipe.get('flagged24h'):
        lines.append(L['flagged'].format(flagged=pipe['flagged24h'], total=pipe['analysesTotal24h']))
    if not pipe['aiConfigured']:
        lines.append(L['ai_off'])
    lines += ['', L['macro_regime'], RULE]
    known_rows = [(region, dim, v) for region in REGION_ORDER for dim in DIMENSIONS.get(region, [])
                  for v in [brief['macroRegime'].get(region, {}).get(dim)] if v and v['known']]
    if known_rows:
        lines += [_regime_line(region, dim, v, lang) for region, dim, v in known_rows]
        unknown = sum(1 for region in REGION_ORDER for dim in DIMENSIONS.get(region, [])
                      if not brief['macroRegime'].get(region, {}).get(dim, {}).get('known'))
        if unknown:
            lines.append(L['unknown_dims'].format(n=unknown))
    else:
        lines.append(L['no_macro_state'])
    lines += [L['risk_liquidity'].format(risk=i18n.state_label('risk_appetite', brief['riskRegime'], lang),
                                         liquidity=i18n.enum(brief['globalLiquidity'], lang))]
    if brief.get('narrative'):
        lines += ['', brief['narrative']]
    elif brief.get('narrativeWithheld'):
        lines += ['', L['narrative_withheld'].format(tokens=', '.join(brief['narrativeWithheld'][:4]))]
    lines += ['', L['upcoming'], RULE]
    highs = [r for r in brief['upcoming'] if r['impact'] == 'HIGH']
    lines += [_release_line(r, lang, L) for r in highs] or [L['none_scheduled']]
    lines += ['', L['developments'], RULE]
    if brief['developments']:
        lines += [f"{i}. [{d['importance']}] {d['title']}\n   {d['summary']}" for i, d in enumerate(brief['developments'], 1)]
    elif not pipe['aiConfigured']:
        lines.append(L['analysis_pending'])
    else:
        lines.append(L['no_threshold_events'])
    if brief.get('officialHeadlines'):
        lines += ['', L['official_sources'], RULE]
        lines += [_headline_line(h, 70, lang) for h in brief['officialHeadlines']]
    if brief.get('topHeadlines'):
        lines += ['', L['top_headlines'], RULE]
        lines += [_headline_line(h, 72, lang, source_limit=24) for h in brief['topHeadlines']]
    lines += ['', L['asset_pressure'], RULE]
    if any(brief['assetPressure'].values()):
        lines += [f'{asset:<8}{score:+d}' if lang == 'en' else f'{asset} {score:+d}'
                  for asset, score in brief['assetPressure'].items()]
    else:
        lines.append(L['all_neutral'])
    lines += ['', L['key_risks'], RULE]
    lines += [f'• {risk}' for risk in brief['keyRisks']] or [f"• {L['none_recorded']}"]
    if i18n.translation_active(lang):
        lines += ['', f"({L['machine_translated']})"]
    return '\n'.join(lines)


def _regime_line(region: str, dim: str, v: dict, lang: str) -> str:
    lamp, arrow, name = light(dim, v['score']), TREND.get(v['trend'], ''), i18n.dimension(dim, lang)
    state = i18n.state_label(dim, v['state'], lang)
    if lang == 'en':
        return f"{lamp} {region:<7}{name:<19}{state:<20}{arrow} {v['score']:+d}"
    return f"{lamp} {region} · {name}៖ {state} {arrow} {v['score']:+d}"


def _release_line(release: dict, lang: str, L: dict) -> str:
    when, forecast, previous = _when(release['scheduledAt'], lang), release['forecast'] or '—', release['previous'] or '—'
    if lang == 'en':
        return (f"{when}  {release['currency']:<4}{release['title'][:34]:<35} "
                f"{L['forecast']} {forecast} · {L['previous']} {previous}")
    return (f"{when} · {release['currency']} {_clip(release['title'], 48)} · "
            f"{L['forecast']} {forecast} · {L['previous']} {previous}")


def _headline_line(headline: dict, limit: int, lang: str, source_limit: int | None = None) -> str:
    """Headlines are the source's own words, in the source's own language - only the layout is localized."""
    source = str(headline['source']).split(' — ')[0]
    source = source[:source_limit] if source_limit else source
    when = _when(headline['publishedAt'], lang)
    if lang == 'en':
        return f"{when}  {_clip(headline['headline'], limit)}  — {source}"
    return f"{when} · {_clip(headline['headline'], limit + 18)} — {source}"


def _clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def _when(value, lang: str = 'en') -> str:
    if isinstance(value, datetime):
        return f'{i18n.weekday(f"{value:%a}", lang)} {value:%H:%M}Z'
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
