from datetime import datetime, timedelta, timezone

from app.intel import aggregate, bias_label
from app.reactions import interpret


def row(score, hours_ago, importance=90, confidence=80):
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    return {'immediate_score': score, 'short_term_score': score, 'medium_term_score': score, 'importance': importance,
            'confidence': confidence, 'created_at': now - timedelta(hours=hours_ago)}


def test_aggregate_decays_old_evidence_and_damps_thin_evidence():
    now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    fresh = aggregate([row(80, 1)], now)
    stale = aggregate([row(80, 200)], now)
    assert fresh['horizons']['immediate']['score'] > stale['horizons']['immediate']['score']
    thin = aggregate([row(80, 1, importance=20, confidence=30)], now)
    assert abs(thin['score']) < abs(fresh['score'])
    assert aggregate([], now)['macroBias'] == 'NEUTRAL'


def test_bias_labels():
    assert bias_label(55) == 'BULLISH' and bias_label(20) == 'SLIGHT_BULLISH'
    assert bias_label(0) == 'NEUTRAL' and bias_label(-20) == 'SLIGHT_BEARISH' and bias_label(-60) == 'BEARISH'


def test_reaction_interpretation():
    assert interpret('BEARISH', -1.2, 'BTC') == 'CONFIRMED'
    assert interpret('BEARISH', 1.2, 'BTC') == 'REJECTED'
    assert interpret('BULLISH', 0.1, 'BTC') == 'FLAT'
    assert interpret('NEUTRAL', 3.0, 'BTC') == 'REJECTED'
