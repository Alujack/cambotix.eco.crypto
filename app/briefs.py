"""Daily macro brief: deterministic structure, optional model-written narrative."""
from datetime import date, datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import ai
from app.config import ASSET_UNIVERSE
from app.intel import LATEST_ANALYSIS, asset_bias, macro_current, upcoming_releases

LIGHTS = {'RISING': '🔺', 'FALLING': '🔻', 'STABLE': '▪️'}


def build_daily(conn, brief_date: date | None = None, use_ai: bool = True) -> dict:
    now = datetime.now(timezone.utc)
    brief_date = brief_date or now.date()
    current = macro_current(conn, now)
    developments = conn.execute(f'''
        SELECT e.id, e.title, e.importance, la.summary, la.key_risks, la.risk_regime_impact, la.relation_to_trend
        FROM economic_events e JOIN ({LATEST_ANALYSIS}) la ON la.event_id = e.id
        WHERE la.created_at >= %s ORDER BY e.importance DESC, la.created_at DESC LIMIT 8''',
        (now - timedelta(hours=24),)).fetchall()
    risks, seen = [], set()
    for row in developments:
        for risk in row['key_risks'] or []:
            key = risk.strip().lower()[:80]
            if key and key not in seen:
                seen.add(key)
                risks.append(risk.strip())
    brief = {
        'kind': 'daily', 'date': brief_date.isoformat(), 'generatedAt': now,
        'macroRegime': {region: {dim: {'state': v['state'], 'trend': v['trend'], 'score': v['score']}
                                 for dim, v in dims.items()} for region, dims in current['regions'].items()},
        'riskRegime': current['riskRegime'], 'globalLiquidity': current['globalLiquidity'],
        'assetPressure': {asset: current['assets'][asset]['score'] for asset in ASSET_UNIVERSE},
        'developments': [{'eventId': r['id'], 'title': r['title'], 'importance': r['importance'], 'summary': r['summary'],
                          'riskRegimeImpact': r['risk_regime_impact'], 'relationToTrend': r['relation_to_trend']}
                         for r in developments],
        'upcoming': [{'currency': r['currency'], 'title': r['title'], 'impact': r['impact'], 'scheduledAt': r['scheduled_at'],
                      'forecast': r['forecast'], 'previous': r['previous']} for r in upcoming_releases(conn, 24, 'MEDIUM')],
        'keyRisks': risks[:6],
    }
    brief['narrative'] = ai.narrate_brief(brief) if use_ai else None
    brief['text'] = render(brief)
    return brief


def render(brief: dict) -> str:
    lines = [f"🌍 GLOBAL MACRO BRIEF — {brief['date']}", '', 'MACRO REGIME', '━' * 18]
    for region in ('US', 'GLOBAL', 'CRYPTO', 'EU'):
        for dim, value in brief['macroRegime'].get(region, {}).items():
            lines.append(f"{region:<7}{dim.replace('_', ' '):<19}{value['state']:<20}{LIGHTS.get(value['trend'], '')} {value['score']:+d}")
    lines += ['', f"Risk regime: {brief['riskRegime']}   Global liquidity: {brief['globalLiquidity']}"]
    if brief.get('narrative'):
        lines += ['', brief['narrative']]
    lines += ['', 'HIGH IMPACT NEXT 24H', '━' * 18]
    lines += [f"{r['scheduledAt']:%H:%M} UTC  {r['currency']}  {r['title']}  (fcst {r['forecast'] or '—'}, prev {r['previous'] or '—'})"
              for r in brief['upcoming'] if r['impact'] == 'HIGH'] or ['(none scheduled)']
    lines += ['', 'MAJOR DEVELOPMENTS', '━' * 18]
    lines += [f"{i}. [{d['importance']}] {d['title']}\n   {d['summary']}" for i, d in enumerate(brief['developments'], 1)] \
        or ['(no analyzed events in the last 24h)']
    lines += ['', 'ASSET MACRO PRESSURE', '━' * 18]
    lines += [f"{asset:<8}{score:+d}" for asset, score in brief['assetPressure'].items()]
    lines += ['', 'KEY RISKS', '━' * 18]
    lines += [f'• {risk}' for risk in brief['keyRisks']] or ['• (none recorded)']
    return '\n'.join(lines)


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
