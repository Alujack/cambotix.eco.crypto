"""The living macro state: a bounded score per region x dimension, moved by evidence-weighted nudges and journaled."""
from app.schemas import Analysis

DIMENSIONS = {
    'US': ['inflation', 'employment', 'growth', 'monetary_policy', 'liquidity', 'fiscal'],
    'EU': ['inflation', 'growth', 'monetary_policy'],
    'GLOBAL': ['risk_appetite', 'geopolitical_risk', 'liquidity', 'energy'],
    'CRYPTO': ['regulation', 'adoption', 'market_structure'],
}
# Labels from most positive score to most negative; thresholds 50 / 20 / -20 / -50.
LABELS = {
    'inflation': ['WELL_ABOVE_TARGET', 'ABOVE_TARGET', 'NEAR_TARGET', 'BELOW_TARGET', 'DEFLATION_RISK'],
    'employment': ['TIGHT', 'HEALTHY', 'BALANCED', 'SOFTENING', 'WEAK'],
    'growth': ['STRONG', 'MODERATE', 'SLUGGISH', 'CONTRACTING', 'RECESSION'],
    'monetary_policy': ['VERY_RESTRICTIVE', 'RESTRICTIVE', 'NEUTRAL', 'ACCOMMODATIVE', 'VERY_ACCOMMODATIVE'],
    'liquidity': ['ABUNDANT', 'AMPLE', 'NEUTRAL', 'TIGHT', 'STRESSED'],
    'fiscal': ['HIGHLY_EXPANSIONARY', 'EXPANSIONARY', 'NEUTRAL', 'TIGHTENING', 'AUSTERITY'],
    'risk_appetite': ['EUPHORIC', 'RISK_ON', 'NEUTRAL', 'RISK_OFF', 'PANIC'],
    'geopolitical_risk': ['EXTREME', 'ELEVATED', 'NORMAL', 'CALM', 'VERY_CALM'],
    'energy': ['SUPPLY_SHOCK', 'TIGHT', 'BALANCED', 'LOOSE', 'GLUT'],
    'regulation': ['SUPPORTIVE', 'CONSTRUCTIVE', 'NEUTRAL', 'RESTRICTIVE', 'HOSTILE'],
    'adoption': ['ACCELERATING', 'GROWING', 'STEADY', 'SLOWING', 'RETREATING'],
    'market_structure': ['ROBUST', 'HEALTHY', 'NEUTRAL', 'FRAGILE', 'STRESSED'],
}


def clamp(value: float, low: int = -100, high: int = 100) -> int:
    return int(max(low, min(high, round(value))))


def weight(importance: int, confidence: int, reliability: int) -> float:
    """How much one event may move the state: 0.05 (noise) .. 0.60 (FOMC/CPI from an official source)."""
    raw = (importance / 100) * (confidence / 100) * (0.5 + 0.5 * reliability / 100) * 0.6
    return round(max(0.05, min(0.6, raw)), 4)


def blend(old_score: int, direction: int, magnitude: int, w: float) -> int:
    if direction == 0:
        return clamp(old_score * (1 - w * 0.25))
    target = direction * max(0, min(100, magnitude))
    return clamp(old_score * (1 - w) + target * w)


# Desk-eye traffic light per label position (most positive score first). Judgement calls, not truth.
LIGHTS = {
    'inflation': ['🔴', '🟡', '🟢', '🟡', '🔴'],
    'employment': ['🟡', '🟢', '🟢', '🟡', '🔴'],
    'growth': ['🟢', '🟢', '🟡', '🔴', '🔴'],
    'monetary_policy': ['🔴', '🟠', '🟡', '🟢', '🟡'],
    'liquidity': ['🟢', '🟢', '🟡', '🔴', '🔴'],
    'fiscal': ['🟡', '🟢', '🟡', '🟠', '🔴'],
    'risk_appetite': ['🟡', '🟢', '🟡', '🔴', '🔴'],
    'geopolitical_risk': ['🔴', '🟠', '🟡', '🟢', '🟢'],
    'energy': ['🔴', '🟠', '🟢', '🟡', '🟡'],
    'regulation': ['🟢', '🟢', '🟡', '🟠', '🔴'],
    'adoption': ['🟢', '🟢', '🟡', '🟠', '🔴'],
    'market_structure': ['🟢', '🟢', '🟡', '🟠', '🔴'],
}


def label_index(score: int) -> int:
    if score >= 50:
        return 0
    if score >= 20:
        return 1
    if score > -20:
        return 2
    if score > -50:
        return 3
    return 4


def state_label(dimension: str, score: int) -> str:
    labels = LABELS.get(dimension) or ['HIGH', 'ELEVATED', 'NEUTRAL', 'LOW', 'VERY_LOW']
    return labels[label_index(score)]


def light(dimension: str, score: int, known: bool = True) -> str:
    if not known:
        return '⚪'
    return (LIGHTS.get(dimension) or ['🟡'] * 5)[label_index(score)]


def trend_label(new_score: int, previous_scores: list[int]) -> str:
    """Compare the new score against the recent path (up to the last five journaled scores)."""
    if not previous_scores:
        return 'STABLE'
    baseline = sum(previous_scores[:5]) / len(previous_scores[:5])
    delta = new_score - baseline
    if delta >= 8:
        return 'RISING'
    if delta <= -8:
        return 'FALLING'
    return 'STABLE'


def apply_updates(conn, event_id: str, event_importance: int, analysis: Analysis, reliability: int) -> list[dict]:
    changes = []
    w = weight(event_importance, analysis.confidence, reliability)
    for update in analysis.macro_state_updates:
        if update.dimension not in DIMENSIONS.get(update.region, []):
            continue
        current = conn.execute('SELECT * FROM macro_state WHERE region = %s AND dimension = %s FOR UPDATE',
                               (update.region, update.dimension)).fetchone()
        if current is None:
            continue
        history = conn.execute('''SELECT new_score FROM macro_state_history WHERE region = %s AND dimension = %s
                                  ORDER BY created_at DESC LIMIT 5''', (update.region, update.dimension)).fetchall()
        new_score = blend(current['score'], update.direction, update.magnitude, w)
        new_state = state_label(update.dimension, new_score)
        new_trend = trend_label(new_score, [row['new_score'] for row in history] or [current['score']])
        new_confidence = clamp(current['confidence'] * (1 - w) + analysis.confidence * w, 0, 100)
        conn.execute('''UPDATE macro_state SET score = %s, state = %s, trend = %s, confidence = %s, last_event_id = %s,
                        updated_at = now() WHERE region = %s AND dimension = %s''',
                     (new_score, new_state, new_trend, new_confidence, event_id, update.region, update.dimension))
        conn.execute('''INSERT INTO macro_state_history (region, dimension, prev_score, new_score, prev_state, new_state,
                        prev_trend, new_trend, event_id, weight, reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                     (update.region, update.dimension, current['score'], new_score, current['state'], new_state,
                      current['trend'], new_trend, event_id, w, update.reason[:500]))
        changes.append({'region': update.region, 'dimension': update.dimension, 'from': current['score'],
                        'to': new_score, 'state': new_state, 'trend': new_trend, 'weight': w})
    return changes
