from datetime import datetime, timezone

from app.briefs import render
from app.macro_state import DIMENSIONS, light


def base(**over):
    regime = {r: {d: {'state': 'UNKNOWN', 'trend': 'STABLE', 'score': 0, 'known': False} for d in dims} for r, dims in DIMENSIONS.items()}
    brief = {'date': '2026-09-09', 'generatedAt': datetime(2026, 9, 9, 6, tzinfo=timezone.utc), 'macroRegime': regime,
             'riskRegime': 'UNKNOWN', 'globalLiquidity': 'UNKNOWN', 'assetPressure': {'USD': 0, 'BTC': 0},
             'developments': [], 'upcoming': [{'currency': 'USD', 'title': 'CPI y/y', 'impact': 'HIGH',
                                               'scheduledAt': datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc), 'forecast': '3.4%', 'previous': '3.4%'}],
             'officialHeadlines': [{'headline': 'Employment Situation Summary', 'source': 'BLS — releases',
                                    'publishedAt': datetime(2026, 9, 8, 12, 30, tzinfo=timezone.utc)}],
             'topHeadlines': [{'headline': 'Fed officials signal patience on cuts', 'source': 'CNBC — Economy',
                               'publishedAt': datetime(2026, 9, 8, 20, tzinfo=timezone.utc), 'prior': 65}],
             'keyRisks': [], 'pipeline': {'received24h': 340, 'sources24h': 18, 'queued': 220, 'extracted24h': 0,
                                          'analyzed24h': 0, 'aiConfigured': False}, 'narrative': None}
    brief.update(over)
    return brief


def test_render_before_any_analysis_explains_itself():
    text = render(base())
    assert 'AI analysis is OFF' in text and '220 queued' in text
    assert 'No macro state yet' in text and 'UNKNOWN             ' not in text
    assert 'Fri 12:30Z  USD CPI y/y' in text and 'fcst 3.4%' in text
    assert 'analysis pending' in text and 'Employment Situation Summary' in text
    assert 'TOP HEADLINES' in text and 'Fed officials signal patience on cuts  — CNBC' in text
    assert 'all assets neutral' in text


def test_render_with_state_uses_lights_and_hides_unknown_rows():
    brief = base()
    brief['macroRegime']['US']['inflation'] = {'state': 'ABOVE_TARGET', 'trend': 'RISING', 'score': 35, 'known': True}
    brief['pipeline']['aiConfigured'] = True
    brief['assetPressure'] = {'USD': 38, 'BTC': -28}
    text = render(brief)
    assert '🟡 US     inflation          ABOVE_TARGET        ↑ +35' in text
    assert 'not yet informed' in text and 'USD     +38' in text and 'BTC     -28' in text
    assert 'no event reached the analysis threshold' in text


def test_lights():
    assert light('inflation', 0) == '🟢' and light('inflation', 60) == '🔴' and light('growth', -60) == '🔴'
    assert light('geopolitical_risk', -60) == '🟢' and light('anything', 0, known=False) == '⚪'


def test_narrative_is_enabled_by_default_and_switchable(monkeypatch):
    """Safety now comes from the groundedness gate, not from disabling prose per provider."""
    from app.config import brief_narrative_enabled
    monkeypatch.delenv('BRIEF_USE_AI', raising=False)
    for provider in ('ollama', 'anthropic', 'mock'):
        monkeypatch.setenv('AI_PROVIDER', provider)
        assert brief_narrative_enabled() is True
    monkeypatch.setenv('BRIEF_USE_AI', 'false')
    assert brief_narrative_enabled() is False


def test_ungrounded_narrative_is_withheld_and_explained(monkeypatch):
    from app import briefs
    brief = base()
    brief['developments'] = [{'eventId': 'e1', 'title': 'US import ban on Canadian goods', 'importance': 75,
                              'summary': 'A stagflationary, risk-off shift.', 'riskRegimeImpact': 'RISK_OFF',
                              'relationToTrend': 'NEW_THEME'}]
    monkeypatch.setattr(briefs.ai, 'narrate_brief', lambda _b: "The BOJ's rate hike drove risk-off sentiment.")
    text, withheld = briefs._narrative(brief, True)
    assert text is None and 'BOJ' in withheld
    brief['narrative'], brief['narrativeWithheld'] = text, withheld
    assert 'Narrative withheld' in render(brief) and 'BOJ' in render(brief)

    monkeypatch.setattr(briefs.ai, 'narrate_brief',
                        lambda _b: 'The US import ban is a risk-off, stagflationary shift for the week.')
    text, withheld = briefs._narrative(brief, True)
    assert withheld is None and text.startswith('The US import ban')
