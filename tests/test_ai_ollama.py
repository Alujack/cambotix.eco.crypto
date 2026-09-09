import json

import httpx
import pytest

from app import ai
from app.schemas import Analysis, Extraction


def test_inline_refs_flattens_defs():
    schema = ai.inline_refs(ai.strict_schema(Analysis))
    assert '$defs' not in json.dumps(schema) and '$ref' not in json.dumps(schema)
    impact = schema['properties']['asset_impacts']['items']
    assert impact['type'] == 'object' and 'immediate' in impact['properties']
    assert impact['properties']['immediate']['properties']['score']['type'] == 'integer'


def _fake_client(handler):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None):
            return handler(url, json)
    return FakeClient


def test_ollama_extract_parses_constrained_json(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'ollama')
    monkeypatch.setenv('OLLAMA_BASE_URL', 'http://ollama.test')
    captured = {}
    content = json.dumps({
        'is_relevant': True, 'event_type': 'CPI_RELEASE', 'subject': 'CPI', 'event_date': '2026-09-11', 'countries': ['US'],
        'categories': ['INFLATION'], 'institutions': ['BLS'], 'people': [], 'assets': ['USD', 'US10Y'], 'metric': 'CPI y/y',
        'actual': 3.4, 'forecast': 3.4, 'previous': 3.4, 'unit': '%', 'surprise': 'IN_LINE', 'tone': 'NOT_APPLICABLE',
        'is_market_reaction_coverage': False, 'importance': 95, 'fact_summary': 'US CPI 3.4% y/y, in line.'})

    def handler(url, body):
        captured.update(url=url, body=body)
        return httpx.Response(200, json={'message': {'role': 'assistant', 'content': content}, 'done': True,
                                         'done_reason': 'stop', 'eval_count': 120, 'prompt_eval_count': 900, 'total_duration': 4e9})
    monkeypatch.setattr(ai.httpx, 'Client', _fake_client(handler))
    extraction = ai.extract({'headline': 'CPI', 'content': '', 'published_at': '2026-09-11'})
    assert isinstance(extraction, Extraction) and extraction.importance == 95
    assert captured['url'] == 'http://ollama.test/api/chat'
    assert captured['body']['model'] == 'llama3.1:8b' and captured['body']['stream'] is False
    assert captured['body']['format']['type'] == 'object' and captured['body']['options']['temperature'] == 0


def test_ollama_errors_are_classified(monkeypatch):
    monkeypatch.setenv('AI_PROVIDER', 'ollama')
    monkeypatch.setattr(ai.httpx, 'Client', _fake_client(lambda u, b: httpx.Response(404, text='model "llama3.1:8b" not found')))
    with pytest.raises(ai.AINotConfigured):
        ai.extract({'headline': 'x', 'content': ''})
    monkeypatch.setattr(ai.httpx, 'Client', _fake_client(lambda u, b: httpx.Response(200, json={'message': {'content': '{"nope": 1}'}, 'done_reason': 'stop'})))
    with pytest.raises(ai.AIError):
        ai.extract({'headline': 'x', 'content': ''})
    monkeypatch.setattr(ai.httpx, 'Client', _fake_client(lambda u, b: httpx.Response(200, json={'message': {'content': '{'}, 'done_reason': 'length'})))
    with pytest.raises(ai.AIError, match='truncated'):
        ai.extract({'headline': 'x', 'content': ''})


def test_model_defaults_follow_provider(monkeypatch):
    from app.config import model_for
    monkeypatch.setenv('AI_PROVIDER', 'ollama')
    monkeypatch.setenv('EXTRACT_MODEL', 'claude-haiku-4-5')  # stale value from an Anthropic setup
    assert model_for('extract') == 'llama3.1:8b'
    monkeypatch.setenv('EXTRACT_MODEL', 'llama3.2:3b')
    assert model_for('extract') == 'llama3.2:3b'
    monkeypatch.setenv('AI_PROVIDER', 'anthropic')
    assert model_for('extract') == 'claude-haiku-4-5'
