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
    assert 'Fed MORE_HAWKISH' in text and 'USD +60/+42' in text and '3 source item(s)' in text
    assert '<hot>' not in text
