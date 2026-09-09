#!/usr/bin/env python3
"""End-to-end smoke test with the offline mock analyst: a synthetic hot CPI print flows
ingest -> extract -> cluster -> analyze -> macro state -> API. Requires the stack running and AI_PROVIDER=mock
(pass --mock to override the running engine's provider for this run via the SMOKE_* env of the engine container)."""
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import read_env  # noqa: E402


def call(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={'X-Eco-Token': token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b'{}')


def main():
    env = read_env()
    base, token = f"http://127.0.0.1:{env.get('ENGINE_PORT', '8020')}", env['ENGINE_TOKEN']
    status, health = call(base, token, 'GET', '/health')
    assert status == 200, health
    if not health['ai']['configured']:
        raise SystemExit('AI not configured: set ANTHROPIC_API_KEY or AI_PROVIDER=mock in .env and restart the engine.')
    stamp = datetime.now(timezone.utc)
    marker = stamp.strftime('%H%M%S')
    articles = {'source': 'bls_cpi', 'items': [
        {'headline': f'SMOKE-{marker} CPI rose 0.4% in August; 12-month inflation 3.1% vs 2.9% forecast, previous 2.8%',
         'url': f'https://www.bls.gov/news.release/cpi.smoke-{marker}.htm', 'publishedAt': stamp.isoformat(),
         'content': 'The Consumer Price Index for All Urban Consumers increased 0.4 percent. Actual 3.1 vs 2.9 forecast, previous 2.8.'},
    ]}
    status, result = call(base, token, 'POST', '/ingest/articles', articles)
    assert status == 200 and result['inserted'] == 1, result
    dup_status, dup = call(base, token, 'POST', '/ingest/articles', articles)
    assert dup['duplicates'] == 1, dup
    print('ingest ok (duplicate rejected)')
    status, result = call(base, token, 'POST', '/ingest/articles', {'source': 'coindesk', 'items': [
        {'headline': f'SMOKE-{marker} Bitcoin falls after hot US CPI print rattles markets', 'publishedAt': stamp.isoformat(),
         'url': f'https://www.coindesk.com/smoke-{marker}', 'content': 'BTC dropped 2% after CPI came in at 3.1% vs 2.9% expected.'}]})
    assert status == 200, result
    status, result = call(base, token, 'POST', '/process/extract?batch=10')
    assert status == 200, result
    print('extract:', {k: v for k, v in result.items() if k in ('claimed', 'extracted', 'ignored', 'errors', 'events_created', 'events_linked')})
    assert result['extracted'] >= 1, result
    deadline = time.time() + 120
    analyzed = None
    while time.time() < deadline:
        status, result = call(base, token, 'POST', '/process/analyze')
        assert status == 200, result
        if result.get('analyzed'):
            analyzed = result
            break
        time.sleep(3)
    assert analyzed, 'no event became due for analysis (check ANALYZE_MIN_IMPORTANCE / debounce)'
    print('analyze:', analyzed['summary'], '| macro changes:', len(analyzed['macroStateChanges']))
    status, detail = call(base, token, 'GET', f"/events/{analyzed['eventId']}")
    assert status == 200 and detail['analysis'], detail
    print('event has', len(detail['articles']), 'linked article(s), roles:', sorted({a['role'] for a in detail['articles']}))
    status, macro = call(base, token, 'GET', '/macro/current')
    assert status == 200 and 'US' in macro['regions'], macro
    print('macro/current:', {k: v['state'] for k, v in macro['regions']['US'].items()})
    status, brief = call(base, token, 'POST', '/briefs/daily')
    assert status == 200 and brief['text'], brief
    print('daily brief rendered,', len(brief['text'].splitlines()), 'lines')
    status, result = call(base, token, 'POST', '/process/reactions')
    assert status == 200, result
    print('reactions:', result)
    print('SMOKE OK')


if __name__ == '__main__':
    main()
