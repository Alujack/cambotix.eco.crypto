"""Read models for the intelligence API: asset macro bias, current macro picture, events, releases."""
import math
from datetime import datetime, timedelta, timezone

from app.config import ASSET_UNIVERSE

HALF_LIFE_HOURS = {'immediate': 12.0, 'short_term': 72.0, 'medium_term': 336.0}
HORIZON_MIX = {'immediate': 0.3, 'short_term': 0.5, 'medium_term': 0.2}
LATEST_ANALYSIS = 'SELECT DISTINCT ON (event_id) * FROM event_analysis ORDER BY event_id, version DESC'


def bias_label(score: int) -> str:
    if score >= 40:
        return 'BULLISH'
    if score >= 15:
        return 'SLIGHT_BULLISH'
    if score > -15:
        return 'NEUTRAL'
    if score > -40:
        return 'SLIGHT_BEARISH'
    return 'BEARISH'


def aggregate(rows: list[dict], now: datetime) -> dict:
    """Decay-weighted blend of per-event impacts. Evidence mass damps a single weak event from looking decisive."""
    horizons = {}
    for horizon, half_life in HALF_LIFE_HOURS.items():
        total, mass = 0.0, 0.0
        for row in rows:
            age_hours = max(0.0, (now - row['created_at']).total_seconds() / 3600)
            w = (row['importance'] / 100) * (row['confidence'] / 100) * math.exp(-age_hours * math.log(2) / half_life)
            total += row[f'{horizon}_score'] * w
            mass += w
        score = (total / mass) * min(1.0, mass / 0.6) if mass > 0 else 0.0
        horizons[horizon] = {'score': int(round(score)), 'direction': bias_label(int(round(score))), 'evidence': round(mass, 3)}
    overall = int(round(sum(horizons[h]['score'] * mix for h, mix in HORIZON_MIX.items())))
    return {'score': overall, 'macroBias': bias_label(overall), 'horizons': horizons}


def asset_bias(conn, asset: str, now: datetime | None = None, days: int = 21) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(f'''
        SELECT ai.asset, ai.immediate_score, ai.short_term_score, ai.medium_term_score, ai.rationale,
               e.id AS event_id, e.title, e.importance, la.confidence, la.created_at, la.summary
        FROM asset_impacts ai
        JOIN ({LATEST_ANALYSIS}) la ON la.id = ai.analysis_id
        JOIN economic_events e ON e.id = ai.event_id
        WHERE ai.asset = %s AND la.created_at >= %s
        ORDER BY la.created_at DESC''', (asset, now - timedelta(days=days))).fetchall()
    result = aggregate(rows, now)
    drivers = sorted(rows, key=lambda r: -abs(r['short_term_score']) * r['importance'])[:5]
    result['asset'] = asset
    result['drivers'] = [{'eventId': r['event_id'], 'title': r['title'], 'importance': r['importance'],
                          'immediate': r['immediate_score'], 'shortTerm': r['short_term_score'],
                          'mediumTerm': r['medium_term_score'], 'rationale': r['rationale'], 'at': r['created_at']}
                         for r in drivers]
    return result


def macro_current(conn, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = conn.execute('SELECT * FROM macro_state ORDER BY region, dimension').fetchall()
    regions: dict[str, dict] = {}
    for row in rows:
        regions.setdefault(row['region'], {})[row['dimension']] = {
            'state': row['state'], 'trend': row['trend'], 'score': row['score'], 'confidence': row['confidence'],
            'lastEventId': row['last_event_id'], 'updatedAt': row['updated_at']}
    global_state = regions.get('GLOBAL', {})
    return {
        'asOf': now,
        'riskRegime': global_state.get('risk_appetite', {}).get('state', 'UNKNOWN'),
        'globalLiquidity': global_state.get('liquidity', {}).get('trend', 'UNKNOWN'),
        'geopoliticalRisk': global_state.get('geopolitical_risk', {}).get('state', 'UNKNOWN'),
        'regions': regions,
        'assets': {asset: asset_bias(conn, asset, now) for asset in ASSET_UNIVERSE},
    }


def recent_events(conn, hours: int = 24, min_importance: int = 0, limit: int = 50) -> list[dict]:
    rows = conn.execute(f'''
        SELECT e.id, e.title, e.event_type, e.event_date, e.countries, e.categories, e.importance, e.status,
               e.article_count, e.first_seen_at, e.last_seen_at, e.facts, e.affected_assets, e.analysis_version,
               la.summary, la.confidence, la.risk_regime_impact, la.relation_to_trend, la.created_at AS analyzed_at
        FROM economic_events e LEFT JOIN ({LATEST_ANALYSIS}) la ON la.event_id = e.id
        WHERE e.last_seen_at >= %s AND e.importance >= %s
        ORDER BY e.importance DESC, e.last_seen_at DESC LIMIT %s''',
        (datetime.now(timezone.utc) - timedelta(hours=hours), min_importance, limit)).fetchall()
    return rows


def event_detail(conn, event_id: str) -> dict | None:
    event = conn.execute('SELECT * FROM economic_events WHERE id = %s', (event_id,)).fetchone()
    if event is None:
        return None
    articles = conn.execute('''SELECT a.id, a.headline, a.url, a.source_key, a.publisher, a.reliability, a.published_at,
                                      ea.role FROM event_articles ea JOIN raw_articles a ON a.id = ea.article_id
                               WHERE ea.event_id = %s ORDER BY a.reliability DESC, a.published_at''', (event_id,)).fetchall()
    analysis = conn.execute('SELECT * FROM event_analysis WHERE event_id = %s ORDER BY version DESC LIMIT 1',
                            (event_id,)).fetchone()
    impacts = reactions = []
    if analysis:
        impacts = conn.execute('SELECT * FROM asset_impacts WHERE analysis_id = %s ORDER BY asset', (analysis['id'],)).fetchall()
        reactions = conn.execute('''SELECT asset, window_label, anchor_price, price, change_pct, expected_direction,
                                           interpretation, due_at, measured_at FROM market_reactions
                                    WHERE event_id = %s ORDER BY asset, due_at''', (event_id,)).fetchall()
    return {'event': event, 'articles': articles, 'analysis': analysis, 'assetImpacts': impacts, 'marketReactions': reactions}


def upcoming_releases(conn, hours: int = 48, min_impact: str = 'MEDIUM') -> list[dict]:
    ranks = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    allowed = [impact for impact, rank in ranks.items() if rank >= ranks.get(min_impact.upper(), 1)]
    now = datetime.now(timezone.utc)
    return conn.execute('''SELECT id, currency, title, impact, scheduled_at, forecast, previous, actual, event_id
                           FROM economic_releases WHERE scheduled_at BETWEEN %s AND %s AND impact = ANY(%s)
                           ORDER BY scheduled_at''', (now - timedelta(hours=1), now + timedelta(hours=hours), allowed)).fetchall()


def analysis_for_asset(conn, asset: str, days: int = 14) -> dict:
    now = datetime.now(timezone.utc)
    bias = asset_bias(conn, asset, now, days=max(days, 21))
    events = conn.execute(f'''
        SELECT e.id, e.title, e.importance, e.event_type, la.summary, la.confidence, la.created_at,
               ai.immediate_score, ai.short_term_score, ai.medium_term_score, ai.rationale
        FROM asset_impacts ai JOIN ({LATEST_ANALYSIS}) la ON la.id = ai.analysis_id
        JOIN economic_events e ON e.id = ai.event_id
        WHERE ai.asset = %s AND la.created_at >= %s ORDER BY la.created_at DESC LIMIT 40''',
        (asset, now - timedelta(days=days))).fetchall()
    reactions = conn.execute('''SELECT event_id, window_label, change_pct, expected_direction, interpretation, measured_at
                                FROM market_reactions WHERE asset = %s AND measured_at IS NOT NULL AND measured_at >= %s
                                ORDER BY measured_at DESC LIMIT 100''', (asset, now - timedelta(days=days))).fetchall()
    scorecard = {}
    for row in reactions:
        bucket = scorecard.setdefault(row['window_label'], {'CONFIRMED': 0, 'REJECTED': 0, 'FLAT': 0})
        bucket[row['interpretation']] += 1
    return {'asset': asset, 'bias': bias, 'events': events, 'reactionScorecard': scorecard, 'asOf': now}
