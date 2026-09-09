from app.clustering import event_id, event_key, event_title, jaccard, slug, tokens
from app.schemas import Extraction


def extraction(**overrides) -> Extraction:
    base = dict(is_relevant=True, event_type='CPI_RELEASE', subject='CPI', event_date='2026-09-09', countries=['US'],
                categories=['INFLATION'], institutions=['BLS'], people=[], assets=['USD', 'XAUUSD'], metric='CPI y/y',
                actual=3.1, forecast=2.9, previous=2.8, unit='%', surprise='ABOVE_EXPECTATIONS', tone='NOT_APPLICABLE',
                is_market_reaction_coverage=False, importance=95, fact_summary='US CPI 3.1% y/y vs 2.9% expected.')
    base.update(overrides)
    return Extraction(**base)


def test_release_key_ignores_subject_but_speech_key_uses_it():
    assert event_key(extraction(subject='Consumer prices')) == event_key(extraction(subject='CPI report'))
    a = event_key(extraction(event_type='FED_SPEECH', subject='Powell'))
    b = event_key(extraction(event_type='FED_SPEECH', subject='Waller'))
    assert a != b and a.endswith('|powell')


def test_event_id_is_deterministic():
    assert event_id('CPI_RELEASE|US|2026-09-09') == event_id('CPI_RELEASE|US|2026-09-09')
    assert event_id('CPI_RELEASE|US|2026-09-09') != event_id('CPI_RELEASE|US|2026-09-10')


def test_lexical_similarity_groups_same_story():
    a = tokens('Fed holds rates steady, signals two cuts this year')
    b = tokens('Federal Reserve holds interest rates steady and signals cuts')
    c = tokens('Bitcoin miners report record hashrate')
    assert jaccard(a, b) > jaccard(a, c)
    assert jaccard(a, c) < 0.1


def test_slug_and_title():
    assert slug('J. Powell — Jackson Hole!') == 'j-powell-jackson-hole'
    assert event_title(extraction(), 'irrelevant headline') == 'US CPI y/y (2026-09-09)'
    assert event_title(extraction(event_type='FED_SPEECH', metric=None), 'Powell speaks') == 'Powell speaks'
