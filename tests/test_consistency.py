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


def test_flags_the_dollar_and_the_euro_rising_together():
    """Observed in a live public post: "USD +40 leaning higher" beside "EURUSD +34 leaning higher".

    EURUSD rising is the dollar falling against the euro, so with conviction on both legs the model has
    contradicted itself. Nothing is rewritten - the flag lets the analysis be discounted downstream.
    """
    analysis = Analysis(**BASE, asset_impacts=[impact('USD', 40, 'haven flows into the dollar'),
                                               impact('EURUSD', 34, 'ECB hike lifts the euro')])
    flags = [f for f in check(analysis) if f['code'] == 'inverse_pair_sign']
    assert len(flags) == 1 and flags[0]['asset'] == 'EURUSD'
    assert 'USD immediate score is +40' in flags[0]['detail']


def test_inverse_pair_allows_a_flat_leg_and_a_proper_inverse():
    """A score inside the noise band is a non-commitment, not a contradiction, and the correct signs must pass."""
    quiet = Analysis(**BASE, asset_impacts=[impact('USD', 40, 'haven flows'), impact('EURUSD', 8, 'little changed')])
    opposed = Analysis(**BASE, asset_impacts=[impact('USD', 40, 'haven flows'), impact('EURUSD', -34, 'euro softer')])
    assert not [f for f in check(quiet) if f['code'] == 'inverse_pair_sign']
    assert not [f for f in check(opposed) if f['code'] == 'inverse_pair_sign']
