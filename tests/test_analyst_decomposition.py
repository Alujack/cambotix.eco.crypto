import json

import pytest

from app import ai
from app.schemas import Analysis, AssetScore, EconomicRead

READ = {
    'summary': 'Tariffs bite into growth while lifting import prices. A stagflationary, risk-off shift.',
    'what_happened': 'The US banned a range of Canadian imports.', 'why_it_matters': 'Import costs rise, output falls.',
    'what_changed_vs_expectations': 'Broader than trailed.', 'is_new_information': True,
    'economic_interpretation': {'inflation': 'HOTTER', 'growth': 'WEAKER', 'employment': 'WEAKER', 'liquidity': 'TIGHTER'},
    'central_bank_implication': {'fed': 'NEUTRAL', 'ecb': 'NOT_RELEVANT', 'rate_cut_probability_impact': 'UNCHANGED'},
    'risk_regime_impact': 'RISK_OFF', 'causal_chain': ['ban', 'import prices up', 'growth down'],
    'relation_to_trend': 'NEW_THEME', 'horizon': 'IMMEDIATE', 'evidence_strength': 'STRONG', 'confidence': 80,
    'affected_assets': ['XAUUSD', 'SPX'],
    'macro_state_updates': [{'region': 'US', 'dimension': 'inflation', 'direction': 1, 'magnitude': 40, 'reason': 'tariffs'}],
    'key_risks': ['further escalation'],
}
CONTEXT = {'event': {'title': 'US import ban', 'facts': {}, 'importance': 75},
           'economic_asset_map': [{'driver': 'TARIFFS_TRADE_WAR', 'asset': 'SPX', 'direction': -1, 'note': None},
                                  {'driver': 'TARIFFS_TRADE_WAR', 'asset': 'XAUUSD', 'direction': 1, 'note': None}]}


def score(value, rationale='channel'):
    return {'immediate': {'direction': 'NEUTRAL', 'score': value}, 'short_term': {'direction': 'NEUTRAL', 'score': value},
            'medium_term': {'direction': 'NEUTRAL', 'score': value // 2}, 'rationale': rationale}


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'ollama')
    monkeypatch.delenv('ANALYST_DECOMPOSE', raising=False)
    return monkeypatch


def stub_calls(monkeypatch, responses):
    """Serve queued payloads in call order and record the systems they were sent to."""
    calls = []

    def fake(model, model_id, system, user, **kwargs):
        calls.append({'model': model.__name__, 'system': system[:24], 'user': user})
        return model.model_validate(responses.pop(0))
    monkeypatch.setattr(ai, '_call', fake)
    return calls


def test_decomposition_makes_one_call_per_affected_asset(ollama):
    calls = stub_calls(ollama, [READ, score(70, 'haven bid'), score(-40, 'margin and demand hit')])
    analysis = ai.analyze(CONTEXT)
    assert [c['model'] for c in calls] == ['EconomicRead', 'AssetScore', 'AssetScore']
    assert isinstance(analysis, Analysis)
    assert {i.asset: i.immediate.score for i in analysis.asset_impacts} == {'XAUUSD': 70, 'SPX': -40}
    assert analysis.summary == READ['summary'] and analysis.risk_regime_impact == 'RISK_OFF'
    # Each scoring call sees only its own asset and that asset's prior.
    spx_call = calls[2]['user']
    assert '"asset": "SPX"' in spx_call and 'TARIFFS_TRADE_WAR' in spx_call and 'XAUUSD' not in json.loads(spx_call)['asset']


def test_sign_contradiction_triggers_one_corrective_retry(ollama):
    calls = stub_calls(ollama, [READ, score(70, 'haven bid'), score(30, 'equities rally'), score(-35, 'demand hit')])
    analysis = ai.analyze(CONTEXT)
    assert len(calls) == 4, 'expected a retry for the RISK_OFF/SPX-positive contradiction'
    assert 'inconsistent' in calls[3]['user'] and 'RISK_OFF' in calls[3]['user']
    assert {i.asset: i.immediate.score for i in analysis.asset_impacts} == {'XAUUSD': 70, 'SPX': -35}
    from app.consistency import check
    assert check(analysis) == [], 'corrected analysis should carry no flags'


def test_retry_kept_when_it_explains_itself(ollama):
    explained = score(30, 'Equities rally despite the risk-off read because the tariff exemption list is wider than feared.')
    stub_calls(ollama, [READ, score(70, 'haven bid'), score(30, 'equities rally'), explained])
    analysis = ai.analyze(CONTEXT)
    spx = next(i for i in analysis.asset_impacts if i.asset == 'SPX')
    assert spx.immediate.score == 30 and 'wider than feared' in spx.rationale


def test_summary_restating_the_event_is_retried(ollama):
    lazy = {**READ, 'summary': READ['what_happened']}
    calls = stub_calls(ollama, [lazy, READ, score(70), score(-40)])
    analysis = ai.analyze(CONTEXT)
    assert calls[1]['model'] == 'EconomicRead' and 'merely restates' in calls[1]['user']
    assert analysis.summary == READ['summary']


def test_a_failing_asset_does_not_lose_the_read(ollama, caplog):
    def fake(model, model_id, system, user, **kwargs):
        if model is EconomicRead:
            return EconomicRead.model_validate(READ)
        if '"asset": "SPX"' in user:
            raise ai.AIError('schema validation failed')
        return AssetScore.model_validate(score(70, 'haven bid'))
    ollama.setattr(ai, '_call', fake)
    analysis = ai.analyze(CONTEXT)
    assert [i.asset for i in analysis.asset_impacts] == ['XAUUSD']
    assert analysis.summary == READ['summary']


def test_anthropic_keeps_the_single_call(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'anthropic')
    monkeypatch.delenv('ANALYST_DECOMPOSE', raising=False)
    from app.config import decompose_analysis
    assert decompose_analysis() is False
    monkeypatch.setenv('ANALYST_DECOMPOSE', 'true')
    assert decompose_analysis() is True
