"""Delivery language: the label tables must stay complete, and prose must fall back to English rather than fail."""
from string import Formatter
from typing import get_args

from app import briefs, i18n, telegram
from app.ai import mock_analyze
from app.macro_state import DIMENSIONS, LABELS as STATE_VOCABULARY
from app.schemas import Analysis, CentralBankImplication, EconomicInterpretation
from tests.test_briefs import base

TRANSLATIONS = {lang: table for lang, table in i18n.LABEL_TABLES.items() if lang != 'en'}


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


def test_label_tables_match_english_keys_and_placeholders():
    """A missing key falls back to English, but a missing or renamed placeholder is a KeyError at delivery time."""
    for lang, table in TRANSLATIONS.items():
        assert set(table) == set(i18n.EN_LABELS), lang
        for key, template in table.items():
            assert _fields(template) == _fields(i18n.EN_LABELS[key]), f'{lang}:{key}'


def test_state_tables_mirror_the_macro_vocabulary():
    """Positional translation only works while the lists line up with app.macro_state.LABELS."""
    for lang, table in i18n.STATE_TABLES.items():
        assert set(table) == set(STATE_VOCABULARY), lang
        for dimension, words in table.items():
            assert len(words) == len(STATE_VOCABULARY[dimension]) == 5, f'{lang}:{dimension}'


def test_enum_and_dimension_tables_cover_what_is_delivered():
    delivered = {'RISING', 'FALLING', 'STABLE', 'IMPROVING', 'DETERIORATING', 'UNKNOWN', 'HIGH', 'MEDIUM', 'LOW'}
    for model in (EconomicInterpretation, CentralBankImplication):
        for field in model.model_fields.values():
            delivered |= set(get_args(field.annotation))
    for name in ('risk_regime_impact', 'relation_to_trend', 'evidence_strength', 'horizon'):
        delivered |= set(get_args(Analysis.model_fields[name].annotation))
    for lang, table in i18n.ENUM_TABLES.items():
        assert not delivered - set(table), (lang, sorted(delivered - set(table)))
    for lang, table in i18n.DIMENSION_TABLES.items():
        assert not {d for dims in DIMENSIONS.values() for d in dims} - set(table), lang


def test_state_labels_are_positional_not_by_word():
    """TIGHT is a tight labour market in one dimension and a liquidity squeeze in another."""
    assert i18n.state_label('employment', 'TIGHT', 'km') != i18n.state_label('liquidity', 'TIGHT', 'km')
    assert i18n.state_label('inflation', 'ABOVE_TARGET', 'en') == 'ABOVE_TARGET'
    assert i18n.state_label('inflation', 'NOT_A_STATE', 'km') == 'NOT_A_STATE'   # a new enum reads English
    assert i18n.enum('MORE_HAWKISH', 'en') == 'MORE_HAWKISH' and i18n.enum('MORE_HAWKISH', 'km') != 'MORE_HAWKISH'
    assert i18n.dimension('monetary_policy', 'en') == 'monetary policy'


def test_output_language_falls_back_to_english(monkeypatch):
    from app.config import output_language
    monkeypatch.delenv('OUTPUT_LANGUAGE', raising=False)
    assert output_language() == 'en'
    monkeypatch.setenv('OUTPUT_LANGUAGE', ' KM ')
    assert output_language() == 'km'
    monkeypatch.setenv('OUTPUT_LANGUAGE', 'fr')      # half-translated output is worse than English
    assert output_language() == 'en'


def test_prose_stays_english_without_an_anthropic_key(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_AUTH_TOKEN', raising=False)
    i18n._CACHE.clear()
    assert i18n.translate(['Inflation is sticky.'], 'km') == ['Inflation is sticky.']
    assert i18n.one(None, 'km') is None


def test_prose_is_cached_and_a_bad_reply_delivers_english(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    i18n._CACHE.clear()
    calls = []

    def fake(texts, language):
        calls.append(list(texts))
        return [f'km:{text}' for text in texts]

    monkeypatch.setattr(i18n.ai, 'translate', fake)
    assert i18n.translate(['one', 'two', 'one'], 'km') == ['km:one', 'km:two', 'km:one']
    assert calls == [['one', 'two']]                       # deduped, and the repeat comes from the cache
    assert i18n.translate(['one'], 'km') == ['km:one'] and len(calls) == 1

    monkeypatch.setattr(i18n.ai, 'translate', lambda texts, language: ['only one'])
    assert i18n.translate(['three', 'four'], 'km') == ['three', 'four']    # never map a shifted list

    def raiser(texts, language):
        raise i18n.ai.AIError('rate limited')

    monkeypatch.setattr(i18n.ai, 'translate', raiser)
    assert i18n.translate(['five'], 'km') == ['five']


def test_khmer_brief_localizes_labels_and_keeps_the_data(monkeypatch):
    monkeypatch.setattr(briefs.i18n, 'translate', lambda texts, lang: [f'km:{t}' for t in texts])
    brief = base()
    brief['macroRegime']['US']['inflation'] = {'state': 'ABOVE_TARGET', 'trend': 'RISING', 'score': 35, 'known': True}
    brief['pipeline']['aiConfigured'] = True
    brief['assetPressure'] = {'USD': 38, 'BTC': -28}
    brief['keyRisks'] = ['Tariff escalation']
    brief['developments'] = [{'eventId': 'e1', 'title': 'US CPI above forecast', 'importance': 90,
                              'summary': 'A hotter print.', 'riskRegimeImpact': 'RISK_OFF', 'relationToTrend': 'CONFIRMS'}]
    text = briefs.render(briefs._localized(brief, 'km'), 'km')

    assert i18n.KM_LABELS['macro_regime'] in text and i18n.KM_LABELS['key_risks'] in text
    assert i18n.KM_STATES['inflation'][1] in text and 'ABOVE_TARGET' not in text
    assert '+35' in text and '↑' in text and 'USD +38' in text and 'BTC -28' in text
    assert 'USD     +38' not in text                       # no character-grid padding outside the <pre> block
    assert 'សុក្រ 12:30Z' in text and 'fcst' not in text     # localized weekday and field labels
    # Verbatim source text is delivered as published; the engine's own prose is translated.
    assert 'Fed officials signal patience on cuts' in text and 'US CPI above forecast' in text
    assert 'km:A hotter print.' in text and '• km:Tariff escalation' in text


def test_khmer_event_alert_keeps_tickers_scores_and_escaping(monkeypatch):
    monkeypatch.setattr(telegram.i18n, 'translate', lambda texts, lang: [f'km:{t}' for t in texts])
    analysis = mock_analyze({'event': {'title': 'US CPI <hot> & sticky', 'categories': ['INFLATION'],
                                       'facts': {'surprise': 'ABOVE_EXPECTATIONS'}}})
    event = {'id': 'evt_x', 'title': 'US CPI <hot> & sticky', 'importance': 95}
    text = telegram.format_event_alert(event, analysis, 3, lang='km')

    assert text.startswith('🔴 <b>US CPI &lt;hot&gt; &amp; sticky</b>') and '<hot>' not in text
    assert i18n.KM_LABELS['impact_now'] in text and i18n.KM_LABELS['chain'] in text
    assert 'USD +60/+42' in text and f"Fed {i18n.KM_ENUMS['MORE_HAWKISH']}" in text
    assert 'MORE_HAWKISH' not in text and 'confidence' not in text
    assert i18n.KM_ENUMS['CONFIRMS'] in text or i18n.KM_ENUMS['NEW_THEME'] in text
    assert 'km:' in text and 'ព័ត៌មានប្រភព 3' in text


def test_translated_prose_is_labelled_as_machine_translated(monkeypatch):
    """A reader must be able to tell a translated sentence from the analyst's own English."""
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_AUTH_TOKEN', raising=False)
    assert not i18n.translation_active('km') and not i18n.translation_active('en')
    assert i18n.KM_LABELS['machine_translated'] not in briefs.render(base(), 'km')

    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setattr(i18n, 'translate', lambda texts, lang: [f'km:{t}' for t in texts])
    assert i18n.translation_active('km')
    assert i18n.KM_LABELS['machine_translated'] in briefs.render(base(), 'km')


def test_english_alert_and_brief_are_untouched_by_the_language_switch(monkeypatch):
    monkeypatch.setenv('OUTPUT_LANGUAGE', 'km')            # lang is passed explicitly, never read mid-render
    assert 'MACRO REGIME' in briefs.render(base())
    assert briefs.render(base()) == briefs.render(base(), 'en')
