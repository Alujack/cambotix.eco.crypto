"""Database-backed pipeline tests (mock AI). scripts/test.sh provides ECO_DATABASE_URL."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import needs_database

pytestmark = needs_database


@pytest.fixture
def client():
    from app.db import database
    from app.main import app
    with database() as conn:
        for table in ('market_reactions', 'asset_impacts', 'event_analysis', 'macro_state_history', 'event_articles',
                      'knowledge_embeddings', 'briefs', 'economic_releases'):
            conn.execute(f'DELETE FROM {table}')
        conn.execute('UPDATE raw_articles SET event_id = NULL')
        conn.execute('DELETE FROM economic_events')
        conn.execute('DELETE FROM raw_articles')
        conn.execute("UPDATE macro_state SET score = 0, state = 'UNKNOWN', trend = 'STABLE', confidence = 0, last_event_id = NULL")
    with TestClient(app) as test_client:
        yield test_client


HEADERS = {'X-Eco-Token': 'test-token'}


def test_requires_token(client):
    assert client.get('/macro/current').status_code == 401
    assert client.get('/health').status_code == 200


def test_pipeline_end_to_end(client):
    stamp = datetime.now(timezone.utc).isoformat()
    body = {'source': 'bls_cpi', 'items': [{'headline': 'CPI rose 0.4% in August; inflation 3.1% vs 2.9% forecast, previous 2.8%',
                                            'url': 'https://www.bls.gov/cpi-test', 'publishedAt': stamp, 'content': 'Actual 3.1 vs 2.9 forecast, previous 2.8.'}]}
    first = client.post('/ingest/articles', json=body, headers=HEADERS).json()
    assert first['inserted'] == 1
    assert client.post('/ingest/articles', json=body, headers=HEADERS).json()['duplicates'] == 1
    reaction = {'source': 'coindesk', 'items': [{'headline': 'Bitcoin falls after hot US CPI print', 'publishedAt': stamp,
                                                 'url': 'https://coindesk.test/1', 'content': 'BTC dropped 2% after CPI came in at 3.1% vs 2.9% expected.'}]}
    assert client.post('/ingest/articles', json=reaction, headers=HEADERS).json()['inserted'] == 1
    noise = {'source': 'decrypt', 'items': [{'headline': 'Top 5 NFT games this weekend', 'publishedAt': stamp, 'url': 'https://decrypt.test/1'}]}
    assert client.post('/ingest/articles', json=noise, headers=HEADERS).json()['inserted'] == 1
    unknown = client.post('/ingest/articles', json={'source': 'nope', 'items': noise['items']}, headers=HEADERS)
    assert unknown.status_code == 422

    result = client.post('/process/extract?batch=10', headers=HEADERS).json()
    assert result['claimed'] == 3 and result['extracted'] == 2 and result['ignored'] == 1
    assert result['events_created'] == 1 and result['events_linked'] == 1

    events = client.get('/events/recent?hours=1', headers=HEADERS).json()
    assert len(events) == 1 and events[0]['article_count'] == 2 and events[0]['importance'] >= 80

    analyzed = client.post('/process/analyze', headers=HEADERS).json()
    assert analyzed['analyzed'] == 1 and analyzed['assetImpacts'] >= 3
    assert any(change['dimension'] == 'inflation' and change['to'] > 0 for change in analyzed['macroStateChanges'])
    assert client.post('/process/analyze', headers=HEADERS).json()['analyzed'] == 0

    detail = client.get(f"/events/{analyzed['eventId']}", headers=HEADERS).json()
    assert {a['role'] for a in detail['articles']} == {'primary', 'reaction'}
    assert detail['analysis']['version'] == 1

    macro = client.get('/macro/current', headers=HEADERS).json()
    assert macro['regions']['US']['inflation']['score'] > 0
    assert macro['assets']['USD']['score'] > 0 > macro['assets']['XAUUSD']['score']
    asset = client.get('/analysis/XAUUSD', headers=HEADERS).json()
    assert asset['bias']['macroBias'] in ('SLIGHT_BEARISH', 'BEARISH') and len(asset['events']) == 1

    brief = client.post('/briefs/daily', headers=HEADERS).json()
    assert 'GLOBAL MACRO BRIEF' in brief['text'] and brief['developments']
    assert client.get('/briefs/latest?format=text', headers=HEADERS).text.startswith('🌍')


def test_calendar_print_spawns_release_article(client):
    scheduled = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    items = [{'title': 'CPI y/y', 'currency': 'USD', 'scheduledAt': scheduled, 'impact': 'High', 'forecast': '2.9%', 'previous': '2.8%'}]
    assert client.post('/ingest/calendar', json={'items': items}, headers=HEADERS).json()['prints'] == 0
    items[0]['actual'] = '3.1%'
    assert client.post('/ingest/calendar', json={'items': items}, headers=HEADERS).json()['prints'] == 1
    assert client.post('/ingest/calendar', json={'items': items}, headers=HEADERS).json()['prints'] == 0
    upcoming = client.get('/events/upcoming?hours=2', headers=HEADERS).json()
    assert upcoming and upcoming[0]['actual'] == '3.1%'
    result = client.post('/process/extract?batch=5', headers=HEADERS).json()
    assert result['extracted'] == 1
    events = client.get('/events/recent?hours=1', headers=HEADERS).json()
    assert events[0]['event_type'] == 'CPI_RELEASE' and events[0]['facts']['actual'] == 3.1
