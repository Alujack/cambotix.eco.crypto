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
             'keyRisks': [], 'assetOutlook': {'material': [], 'quiet': ['USD', 'BTC']},
             'pipeline': {'received24h': 340, 'sources24h': 18, 'queued': 220, 'extracted24h': 0,
                          'analyzed24h': 0, 'aiConfigured': False}, 'narrative': None}
    brief.update(over)
    return brief


def outlook_row(**over):
    row = {'asset': 'XAUUSD', 'name': 'Gold', 'upMeans': 'gold price higher', 'score': -38,
           'direction': 'SLIGHT_BEARISH', 'path': 'FADING', 'evidence': 'EVIDENCE_SOLID', 'eventCount': 4,
           'horizons': {'immediate': {'score': -52, 'direction': 'BEARISH'},
                        'short_term': {'score': -40, 'direction': 'BEARISH'},
                        'medium_term': {'score': -14, 'direction': 'SLIGHT_BEARISH'}},
           'drivers': [{'eventId': 'evt_cpi', 'title': 'US CPI above the consensus', 'importance': 95,
                        'rationale': 'Real yields rise and gold pays no coupon to offset that.'}],
           'trackRecord': None}
    row.update(over)
    return row


def test_render_before_any_analysis_explains_itself():
    text = render(base())
    assert 'AI analysis is OFF' in text and '220 queued' in text
    assert 'No macro state yet' in text and 'UNKNOWN             ' not in text
    assert 'Fri 12:30Z  USD CPI y/y' in text and 'fcst 3.4%' in text
    assert 'analysis pending' in text and 'Employment Situation Summary' in text
    assert 'TOP HEADLINES' in text and 'Fed officials signal patience on cuts  — CNBC' in text
    assert 'no analyzed event has given any asset a direction yet' in text


def test_render_with_state_uses_lights_and_hides_unknown_rows():
    brief = base()
    brief['macroRegime']['US']['inflation'] = {'state': 'ABOVE_TARGET', 'trend': 'RISING', 'score': 35, 'known': True}
    brief['pipeline']['aiConfigured'] = True
    brief['assetPressure'] = {'USD': 38, 'BTC': -28}
    text = render(brief)
    assert '🟡 US     inflation:          above target and rising ↑ (+35)' in text
    assert 'not yet informed' in text
    assert 'no event reached the analysis threshold' in text


def test_regime_line_never_says_the_same_word_twice():
    """A STEADY state on a STABLE trend read as "steady and steady" in both languages."""
    brief = base()
    brief['macroRegime']['CRYPTO']['adoption'] = {'state': 'STEADY', 'trend': 'STABLE', 'score': -2, 'known': True}
    text = render(brief)
    assert 'adoption:           steady → (-2)' in text and 'steady and steady' not in text


def test_outlook_section_gives_direction_horizon_reason_and_conviction():
    """The section exists so a reader never has to interpret a bare score: -38 on gold is not an instruction."""
    brief = base(assetOutlook={'material': [outlook_row()], 'quiet': ['ETH', 'OIL']})
    text = render(brief)
    assert 'Gold (XAUUSD) — leaning lower, strongest now and fading over the following weeks' in text
    assert 'Next 4h -52  ·  1-5 days -40  ·  2-8 weeks -14   (gold price higher)' in text
    assert 'Why: Real yields rise and gold pays no coupon to offset that.' in text
    assert 'from "US CPI above the consensus" (importance 95)' in text
    assert 'Evidence solid, from 4 analyzed event(s)' in text
    assert 'Flat, nothing to act on: ETH, OIL' in text
    assert "the engine's past calls here" not in text          # no track record yet, so nothing is claimed


def test_outlook_publishes_the_engines_own_hit_rate_only_when_it_has_one():
    row = outlook_row(trackRecord={'confirmed': 7, 'rejected': 4, 'flat': 2, 'checks': 11, 'days': 30, 'hitRate': 64})
    text = render(base(assetOutlook={'material': [row], 'quiet': []}))
    assert "the engine's past calls here: 7 of 11 confirmed by the actual price move (30d)" in text
    thin = outlook_row(trackRecord={'confirmed': 2, 'rejected': 0, 'flat': 1, 'checks': 2, 'days': 30, 'hitRate': None})
    assert "past calls" not in render(base(assetOutlook={'material': [thin], 'quiet': []}))


def test_outlook_reads_the_us10y_sign_out_loud():
    """A positive US10Y score is the yield rising, which is the one sign readers invert."""
    row = outlook_row(asset='US10Y', name='US 10-year yield', upMeans='yield higher — bond prices lower',
                      score=44, direction='BULLISH', path='STEADY',
                      horizons={'immediate': {'score': 46, 'direction': 'BULLISH'},
                                'short_term': {'score': 44, 'direction': 'BULLISH'},
                                'medium_term': {'score': 40, 'direction': 'BULLISH'}})
    text = render(base(assetOutlook={'material': [row], 'quiet': []}))
    assert 'US 10-year yield (US10Y) — expected higher, holding across all three horizons' in text
    assert '(yield higher — bond prices lower)' in text


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
    brief['assetOutlook'] = {'material': [outlook_row()], 'quiet': []}
    monkeypatch.setattr(briefs.ai, 'narrate_brief', lambda _b: "The BOJ's rate hike drove risk-off sentiment.")
    text, withheld = briefs._narrative(brief, True)
    assert text is None and 'BOJ' in withheld
    brief['narrative'], brief['narrativeWithheld'] = text, withheld
    assert 'Narrative withheld' in render(brief) and 'BOJ' in render(brief)

    monkeypatch.setattr(briefs.ai, 'narrate_brief',
                        lambda _b: 'The US import ban is a risk-off, stagflationary shift for the week.')
    text, withheld = briefs._narrative(brief, True)
    assert withheld is None and text.startswith('The US import ban')

    # The outlook is part of the model's input, so the events and reasons in it are ground the narrative may stand on.
    monkeypatch.setattr(briefs.ai, 'narrate_brief',
                        lambda _b: 'Gold stays pressured while US CPI runs above the consensus.')
    assert briefs._narrative(brief, True)[1] is None
