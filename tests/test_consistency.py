from app.consistency import check
from app.schemas import Analysis

BASE = dict(summary='Tariffs bite.', what_happened='US bans imports.', why_it_matters='Stagflationary.',
            what_changed_vs_expectations='Unexpected', is_new_information=True,
            economic_interpretation={'inflation': 'HOTTER', 'growth': 'WEAKER', 'employment': 'WEAKER', 'liquidity': 'TIGHTER'},
            central_bank_implication={'fed': 'NEUTRAL', 'ecb': 'NOT_RELEVANT', 'rate_cut_probability_impact': 'UNCHANGED'},
            risk_regime_impact='RISK_OFF', causal_chain=['a', 'b', 'c'], relation_to_trend='NEW_THEME', horizon='IMMEDIATE',
            evidence_strength='STRONG', confidence=80, macro_state_updates=[], key_risks=['escalation'])


def impact(asset, score, rationale='specific channel'):
    return {'asset': asset, 'immediate': {'direction': 'NEUTRAL', 'score': score},
            'short_term': {'direction': 'NEUTRAL', 'score': score}, 'medium_term': {'direction': 'NEUTRAL', 'score': 0},
            'rationale': rationale}


def test_flags_risk_off_with_bullish_equities():
    # Distinct rationales so only the sign contradiction is under test (this is the llama3.1:8b failure observed live).
    analysis = Analysis(**BASE, asset_impacts=[impact('SPX', 30, 'equity multiple'), impact('BTC', 20, 'risk proxy'),
                                               impact('XAUUSD', 80, 'haven bid')])
    flags = check(analysis)
    assert {f['code'] for f in flags} == {'risk_regime_sign'}
    assert {f['asset'] for f in flags} == {'SPX', 'BTC'}


def test_clean_analysis_has_no_flags():
    analysis = Analysis(**BASE, asset_impacts=[impact('SPX', -30, 'demand and margin hit'),
                                               impact('XAUUSD', 60, 'haven bid'), impact('USD', 40, 'safety flows')])
    assert check(analysis) == []


def test_flags_zero_score_with_directional_rationale_and_duplicates():
    analysis = Analysis(**BASE, asset_impacts=[impact('USD', 0, 'USD may rise as a haven'),
                                               impact('XAUUSD', 0, 'USD may rise as a haven'),
                                               impact('OIL', 0, 'USD may rise as a haven')])
    codes = {f['code'] for f in check(analysis)}
    assert 'zero_with_direction' in codes and 'duplicate_rationale' in codes


def test_flags_summary_repeating_the_event():
    analysis = Analysis(**{**BASE, 'summary': 'US bans imports.'}, asset_impacts=[impact('USD', 20)])
    assert any(f['code'] == 'summary_repeats_event' for f in check(analysis))
