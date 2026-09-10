from app.ai import mock_analyze
from app.telegram import chunks, format_event_alert


def test_chunks_respect_limit_and_lines():
    text = '\n'.join(f'line {i} ' + 'x' * 50 for i in range(200))
    parts = chunks(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert '\n'.join(parts) == text
    assert chunks('short') == ['short']
    assert all(len(p) <= 10 for p in chunks('a' * 25, limit=10))


def test_event_alert_is_escaped_html():
    analysis = mock_analyze({'event': {'title': 'US CPI <hot> & sticky', 'categories': ['INFLATION'],
                                       'facts': {'surprise': 'ABOVE_EXPECTATIONS'}}})
    event = {'id': 'evt_x', 'title': 'US CPI <hot> & sticky', 'importance': 95}
    text = format_event_alert(event, analysis, 3)
    assert text.startswith('🔴 <b>US CPI &lt;hot&gt; &amp; sticky</b>')
    assert 'Fed more hawkish' in text and 'overall risk-off' in text and '3 source item(s)' in text
    assert '<hot>' not in text


def test_event_alert_gives_each_asset_a_direction_a_path_and_a_reason():
    """A phone alert that says "BTC -45" gives the reader nothing to act on."""
    from app.telegram import ALERT_ASSETS
    analysis = mock_analyze({'event': {'title': 'US CPI 3.4% y/y', 'categories': ['INFLATION'],
                                       'facts': {'surprise': 'ABOVE_EXPECTATIONS'}}})
    text = format_event_alert({'id': 'evt_x', 'title': 'US CPI 3.4% y/y', 'importance': 95}, analysis, 3)
    assert '<b>What this means for each market</b>' in text
    assert '\u2022 US dollar (USD) \u2014 expected higher, strongest now and fading over the following weeks' in text
    assert 'next 4h +60 \u00b7 1-5 days +42 \u00b7 2-8 weeks +18 \u2014 On a hotter-than-expected print' in text
    assert '\u2022 Gold (XAUUSD) \u2014 expected lower' in text and 'pays no coupon' in text
    # Each asset argues its own channel, and the alert stays short enough to read on a phone.
    assert text.count('\u2022 ') == ALERT_ASSETS < len(analysis.asset_impacts)
    assert len([line for line in text.split('\n') if line.startswith('   next 4h')]) == ALERT_ASSETS


def test_event_alert_clips_a_runaway_rationale():
    from app.telegram import RATIONALE_LIMIT
    analysis = mock_analyze({'event': {'title': 'US CPI 3.4% y/y', 'categories': ['INFLATION'],
                                       'facts': {'surprise': 'ABOVE_EXPECTATIONS'}}})
    analysis.asset_impacts[0].rationale = 'word ' * 200
    text = format_event_alert({'id': 'evt_x', 'title': 'US CPI 3.4% y/y', 'importance': 95}, analysis, 3)
    assert '\u2026' in text and 'word ' * 60 not in text
    assert max(len(line) for line in text.split('\n')) < RATIONALE_LIMIT + 160


def test_pre_chunks_are_wrapped_and_escaped():
    from app.telegram import render_chunks
    parts = render_chunks('a <b> & c\n' * 800, 'HTML_PRE')
    assert len(parts) > 1
    assert all(p.startswith('<pre>') and p.endswith('</pre>') and len(p) <= 3900 for p in parts)
    assert '&lt;b&gt; &amp;' in parts[0] and '<b>' not in parts[0]
    assert render_chunks('plain', None) == ['plain']


def test_bot_token_never_reaches_the_logs(caplog, monkeypatch):
    """httpx logs request URLs at INFO and the token lives in the Telegram URL path."""
    import logging

    import app.telegram as telegram_module
    assert logging.getLogger('httpx').level >= logging.WARNING
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123456:SUPERSECRETVALUE')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '42')
    with caplog.at_level(logging.DEBUG):
        try:
            telegram_module.send('hello')
        except telegram_module.TelegramError:
            pass
    assert 'SUPERSECRETVALUE' not in caplog.text
