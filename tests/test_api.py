"""Database-backed pipeline tests (mock AI). scripts/test.sh provides ECO_DATABASE_URL."""
from datetime import date, datetime, timezone

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
                      'knowledge_embeddings', 'briefs', 'economic_releases', 'notifications'):
            conn.execute(f'DELETE FROM {table}')
        conn.execute("UPDATE macro_state SET score = 0, state = 'UNKNOWN', trend = 'STABLE', confidence = 0, last_event_id = NULL")
        conn.execute('UPDATE raw_articles SET event_id = NULL')
        conn.execute('DELETE FROM economic_events')
        conn.execute('DELETE FROM raw_articles')
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
    assert len(events) == 1 and events[0]['article_count'] == 2 and events[0]['importance'] >= 60

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

    # The same intelligence as content to post: rebuilt from the database, no model call, ready to paste.
    assert set(brief['social']) == set(('telegram', 'facebook'))
    post = client.get('/social/daily?platform=facebook', headers=HEADERS).json()
    assert post['parseMode'] is None and '<' not in post['text'] and '#Macro' in post['text']
    assert 'WHAT IT MEANS FOR MARKETS' in post['text'] and 'not trading advice' in post['text']
    assert client.get('/social/daily?format=text', headers=HEADERS).text.startswith('<b>🌍 GLOBAL MACRO')
    event_post = client.get(f"/social/event/{analyzed['eventId']}?format=text", headers=HEADERS).text
    assert 'US CPI' in event_post and 'not trading advice' in event_post and '#Inflation' in event_post
    assert client.get('/social/event/evt_missing', headers=HEADERS).status_code == 404
    assert client.get('/social/daily?platform=myspace', headers=HEADERS).status_code == 422
    assert client.get('/social/daily?lang=fr', headers=HEADERS).status_code == 422


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


def test_outbox_delivers_and_retries(client, monkeypatch):
    from app import telegram
    from app.db import database
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'x')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '1')
    with database() as conn:
        telegram.enqueue(conn, 'test', 'a', 'hello')
        telegram.enqueue(conn, 'test', 'b', 'world')
    calls = []

    def flaky(text, parse_mode=None, target=None):
        calls.append(text)
        if text == 'world':
            raise telegram.TelegramError('rate limited by Telegram')
    result = telegram.deliver_pending(sender=flaky)
    assert result == {'claimed': 2, 'sent': 1, 'failed': 1} and calls == ['hello', 'world']
    status = client.get('/notify/status', headers=HEADERS).json()
    assert status['sent'] == 1 and status['pending'] == 1
    with database() as conn:
        conn.execute("UPDATE notifications SET next_attempt_at = now() WHERE status = 'pending'")
    assert telegram.deliver_pending(sender=lambda t, p=None, target=None: None)['sent'] == 1
    assert client.get('/notify/status', headers=HEADERS).json()['pending'] == 0


def test_a_social_post_is_delivered_to_its_own_channel(client, monkeypatch):
    """The operator's chat and the public channel share one outbox, so a row carries its own destination."""
    from app import telegram
    from app.db import database
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'x')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '1')
    monkeypatch.setenv('TELEGRAM_CHANNEL_ID', '@cambotix_macro')
    assert telegram.channel_configured()
    with database() as conn:
        telegram.enqueue(conn, 'brief', 'daily:2026-09-09', 'the full brief', 'HTML_PRE')
        telegram.enqueue(conn, 'social_brief', 'daily:2026-09-09', 'the post', 'HTML', target=telegram.channel_id())
    seen = []
    result = telegram.deliver_pending(sender=lambda text, parse_mode=None, target=None: seen.append((text, target)))
    assert result['sent'] == 2 and ('the post', '@cambotix_macro') in seen and ('the full brief', None) in seen
    assert client.get('/notify/status', headers=HEADERS).json()['channelId'] == '@cambotix_macro'


def test_interrupted_claims_are_swept_back(client):
    """A restart mid-batch leaves rows claimed; the sweepers must return them or the queue stalls silently."""
    from app.db import database
    from app.pipeline import claim_articles, claim_event
    stamp = datetime.now(timezone.utc).isoformat()
    client.post('/ingest/articles', headers=HEADERS, json={'source': 'bls_cpi', 'items': [
        {'headline': 'CPI rose 3.1% vs 2.9% forecast, previous 2.8%', 'url': 'https://bls.test/sweep', 'publishedAt': stamp}]})
    with database() as conn:
        conn.execute("UPDATE raw_articles SET status='extracting', next_attempt_at=now() - interval '25 minutes'")
    assert len(claim_articles(5)) == 1, 'stale extracting row was not returned to the queue'
    # That direct claim now owns the row; hand it back so the endpoint can extract it and create the event.
    with database() as conn:
        conn.execute("UPDATE raw_articles SET status='queued', next_attempt_at=now()")
    assert client.post('/process/extract?batch=5', headers=HEADERS).json()['extracted'] == 1
    with database() as conn:
        conn.execute("UPDATE economic_events SET status='analyzing', needs_analysis=true, "
                     "next_attempt_at=now() - interval '35 minutes'")
    assert claim_event() is not None, 'stale analyzing event was not returned to the queue'
    with database() as conn:
        conn.execute("UPDATE economic_events SET status='analyzing', next_attempt_at=now()")
    assert claim_event() is None, 'a fresh claim must not be stolen'


def test_health_makes_no_outbound_call(client, monkeypatch):
    """The container healthcheck runs /health every 10s with a 3s budget: a probe here fails it when the model is busy."""
    import app.main as main_module

    def explode(*args, **kwargs):
        raise AssertionError('/health must not make a network call')
    monkeypatch.setattr(main_module, 'ollama_status', explode)
    monkeypatch.setenv('AI_PROVIDER', 'ollama')
    body = client.get('/health').json()
    assert body['status'] == 'ok' and body['ai']['provider'] == 'ollama' and 'reachable' not in body['ai']
    monkeypatch.setattr(main_module, 'ollama_status', lambda: {'ollama': 'http://x', 'reachable': True, 'missingModels': []})
    assert client.get('/ai/status', headers=HEADERS).json()['reachable'] is True


def test_if_missing_builds_the_brief_once_then_skips(client):
    """The watchdog (Eco 13) catches a daily cron that never fired, so it must be a no-op once the brief is there.

    A missed 06:00 run leaves no n8n execution row and no error, so nothing else would notice.
    """
    first = client.post('/briefs/daily?if_missing=true&notify=false', headers=HEADERS).json()
    assert 'GLOBAL MACRO BRIEF' in first['text']
    second = client.post('/briefs/daily?if_missing=true&notify=false', headers=HEADERS).json()
    # With nothing to deliver, an existing brief row is the whole job - otherwise the watchdog would rebuild hourly.
    assert second == {'skipped': 'already built', 'date': first['date']}
    # Telegram is unconfigured here, so there is still nothing to deliver and the brief alone settles it.
    assert client.post('/briefs/daily?if_missing=true', headers=HEADERS).json()['skipped'] == 'already delivered'
    # Without the flag the endpoint still rebuilds on demand, which is what the 06:00 workflow relies on.
    assert 'GLOBAL MACRO BRIEF' in client.post('/briefs/daily?notify=false', headers=HEADERS).json()['text']


def test_a_built_but_undelivered_brief_is_not_done(client, monkeypatch):
    """The case the watchdog exists for: the brief row is there but its message never reached Telegram.

    Checked against _brief_done directly - driving it through the endpoint would need a live bot token.
    """
    from app import telegram
    from app.db import database
    from app.main import _brief_done
    built = client.post('/briefs/daily?notify=false', headers=HEADERS).json()
    day = date.fromisoformat(built['date'])
    monkeypatch.setattr(telegram, 'configured', lambda: True)
    with database() as conn:
        assert _brief_done(conn, day, notify=False) is True        # nothing was asked to be delivered
        assert _brief_done(conn, day, notify=True) is False        # no notification row at all
        for status, done in (('pending', False), ('failed', False), ('sent', True)):
            conn.execute('''INSERT INTO notifications (kind, ref_id, text, status) VALUES ('brief', %s, 'x', %s)
                            ON CONFLICT (kind, ref_id) DO UPDATE SET status = EXCLUDED.status''',
                         (f'daily:{day.isoformat()}', status))
            assert _brief_done(conn, day, notify=True) is done, status
