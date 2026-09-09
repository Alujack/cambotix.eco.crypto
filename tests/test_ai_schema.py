import json

from app.ai import mock_analyze, mock_extract, strict_schema
from app.schemas import Analysis, AssetImpact, Extraction


def _walk(node, seen):
    if isinstance(node, dict):
        seen.append(node)
        for value in node.values():
            _walk(value, seen)
    elif isinstance(node, list):
        for value in node:
            _walk(value, seen)


def test_strict_schema_closes_objects_and_drops_constraints():
    for model in (Extraction, Analysis):
        schema = strict_schema(model)
        nodes = []
        _walk(schema, nodes)
        for node in nodes:
            if node.get('type') == 'object' and 'properties' in node:
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for forbidden in ('minimum', 'maximum', 'minLength', 'maxLength', 'format', 'default'):
                assert forbidden not in node
        json.dumps(schema)


def test_asset_impact_direction_follows_score():
    impact = AssetImpact(asset='BTC', immediate={'direction': 'BULLISH', 'score': -60},
                         short_term={'direction': 'BULLISH', 'score': 5}, medium_term={'direction': 'NEUTRAL', 'score': 40},
                         rationale='x')
    assert impact.immediate.direction == 'BEARISH'
    assert impact.short_term.direction == 'NEUTRAL'
    assert impact.medium_term.direction == 'BULLISH'


def test_mock_extract_reads_numbers_and_type():
    article = {'headline': 'CPI rose 3.1% vs 2.9% forecast, previous 2.8%', 'content': '', 'categories': ['INFLATION'],
               'countries': ['US'], 'assets': ['USD'], 'entities': ['BLS'], 'importance_prior': 80}
    extraction = mock_extract(article)
    assert extraction.event_type == 'CPI_RELEASE'
    assert (extraction.actual, extraction.forecast, extraction.previous) == (3.1, 2.9, 2.8)
    assert extraction.surprise == 'ABOVE_EXPECTATIONS'


def test_mock_analyze_hot_inflation_is_hawkish():
    analysis = mock_analyze({'event': {'title': 'US CPI', 'categories': ['INFLATION'], 'facts': {'surprise': 'ABOVE_EXPECTATIONS'}}})
    assert analysis.economic_interpretation.inflation == 'HOTTER'
    assert analysis.central_bank_implication.fed == 'MORE_HAWKISH'
    scores = {impact.asset: impact.immediate.score for impact in analysis.asset_impacts}
    assert scores['USD'] > 0 > scores['XAUUSD']
    assert analysis.asset_impacts[0].immediate.direction == 'BULLISH'
