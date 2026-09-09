from app.grounding import ungrounded_claims

BRIEF = {
    'riskRegime': 'NEUTRAL', 'globalLiquidity': 'STABLE',
    'macroRegime': {'US': {'inflation': {'state': 'NEAR_TARGET', 'trend': 'STABLE', 'score': 9, 'known': True}}},
    'assetPressure': {'USD': 24, 'XAUUSD': 15},
    'developments': [{'title': 'U.S. reveals import ban on Canadian goods', 'summary': 'The US imposes an import ban, '
                      'a stagflationary risk-off event.', 'riskRegimeImpact': 'RISK_OFF', 'relationToTrend': 'NEW_THEME'}],
    'upcoming': [{'title': 'Main Refinancing Rate', 'currency': 'EUR', 'impact': 'HIGH', 'forecast': '2.65%',
                  'previous': '2.40%'}],
    'officialHeadlines': [], 'topHeadlines': [], 'keyRisks': ['Escalation of trade tensions'],
}


def test_grounded_narrative_passes():
    text = ('The regime is NEUTRAL with liquidity STABLE. The US import ban on Canadian goods is a risk-off, '
            'stagflationary development, and gold carries positive pressure into the ECB Main Refinancing Rate.')
    assert ungrounded_claims(text, BRIEF) == ['ECB']  # ECB is not in today's data; only the release title is


def test_fabricated_event_is_caught():
    """The real llama3.1:8b failure: a BOJ rate hike that appeared nowhere in the input."""
    text = ("The BOJ's decision to hike rates in Japan has been a key driver of risk-off sentiment, with the yen "
            "and JGB yields strengthening.")
    missing = ungrounded_claims(text, BRIEF)
    assert 'BOJ' in missing and 'Japan' in missing and 'JGB' in missing


def test_invented_numbers_are_caught():
    assert '7.5%' in ungrounded_claims('Inflation is running at 7.5% year over year.', BRIEF)
    assert ungrounded_claims('The forecast is 2.65% versus 2.40% previously.', BRIEF) == []


def test_empty_and_safe_text():
    assert ungrounded_claims('', BRIEF) == []
    assert ungrounded_claims('Markets are quiet today. The week ahead looks thin.', BRIEF) == []
