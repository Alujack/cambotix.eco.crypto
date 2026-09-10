#!/usr/bin/env python3
"""End-to-end smoke test with the offline mock analyst, isolated from production data: a throwaway engine container
(port 8021, AI_PROVIDER=mock) runs against a scratch `eco_smoke` database, and a synthetic hot CPI print flows
ingest -> extract -> cluster -> analyze -> macro state -> brief -> API. Requires the stack to be running."""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import read_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PORT = 8021
DB = 'eco_smoke'


def sh(*args, stdin=None, check=True):
    return subprocess.run(args, cwd=ROOT, input=stdin, text=True, capture_output=True, check=check)


def call(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method,
                                     headers={'X-Eco-Token': token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b'{}')


def start_engine(env):
    sh('docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'eco', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1',
       '-c', f'DROP DATABASE IF EXISTS {DB};', '-c', f'CREATE DATABASE {DB};')
    sh('docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'eco', '-d', DB, '-v', 'ON_ERROR_STOP=1', '-q',
       stdin=(ROOT / 'db' / '02-schema.sql').read_text())
    url = f"postgresql://eco:{env['POSTGRES_PASSWORD']}@postgres:5432/{DB}"
    # OUTPUT_LANGUAGE=en keeps the run offline and its assertions deterministic: a Khmer delivery would call out to
    # the translation model. The localized render is covered by tests/test_i18n.py instead.
    container = sh('docker', 'compose', 'run', '-d', '--rm', '--no-deps', '-e', 'AI_PROVIDER=mock', '-e', 'EMBEDDINGS_PROVIDER=none',
                   '-e', 'OUTPUT_LANGUAGE=en', '-e', f'ECO_DATABASE_URL={url}', '-p', f'127.0.0.1:{PORT}:8000',
                   'engine').stdout.strip()
    base = f'http://127.0.0.1:{PORT}'
    for _ in range(60):
        try:
            with urllib.request.urlopen(base + '/health', timeout=3) as response:
                if response.status == 200:
                    return container, base
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    subprocess.run(['docker', 'stop', container], capture_output=True)
    raise SystemExit('smoke engine did not become healthy; see `docker compose logs engine`')


def scenario(base, token):
    stamp = datetime.now(timezone.utc)
    articles = {'source': 'bls_cpi', 'items': [
        {'headline': 'CPI rose 0.4% in August; 12-month inflation 3.1% vs 2.9% forecast, previous 2.8%',
         'url': 'https://www.bls.gov/news.release/cpi.smoke.htm', 'publishedAt': stamp.isoformat(),
         'content': 'The Consumer Price Index for All Urban Consumers increased 0.4 percent. Actual 3.1 vs 2.9 forecast, previous 2.8.'}]}
    status, result = call(base, token, 'POST', '/ingest/articles', articles)
    assert status == 200 and result['inserted'] == 1, result
    assert call(base, token, 'POST', '/ingest/articles', articles)[1]['duplicates'] == 1
    print('ingest ok (duplicate rejected)')
    status, result = call(base, token, 'POST', '/ingest/articles', {'source': 'coindesk', 'items': [
        {'headline': 'Bitcoin falls after hot US CPI print rattles markets', 'publishedAt': stamp.isoformat(),
         'url': 'https://www.coindesk.com/smoke', 'content': 'BTC dropped 2% after CPI came in at 3.1% vs 2.9% expected.'}]})
    assert status == 200 and result['inserted'] == 1, result
    status, result = call(base, token, 'POST', '/ingest/articles', {'source': 'decrypt', 'items': [
        {'headline': 'Top 5 NFT games to play this weekend', 'publishedAt': stamp.isoformat(), 'url': 'https://decrypt.co/smoke'}]})
    assert status == 200 and result['inserted'] == 1, result
    status, result = call(base, token, 'POST', '/process/extract?batch=10')
    assert status == 200, result
    print('extract:', {k: v for k, v in result.items() if k != 'detail'})
    assert result['extracted'] == 2 and result['ignored'] == 1 and result['events_created'] == 1, result
    # force=true skips the coverage debounce so the smoke run does not wait ANALYZE_DEBOUNCE_SECONDS.
    status, analyzed = call(base, token, 'POST', '/process/analyze?force=true')
    assert status == 200 and analyzed.get('analyzed') == 1, analyzed
    print('analyze:', analyzed['summary'], '| asset impacts:', analyzed['assetImpacts'], '| macro changes:', len(analyzed['macroStateChanges']))
    status, detail = call(base, token, 'GET', f"/events/{analyzed['eventId']}")
    assert status == 200 and detail['analysis'], detail
    print('event has', len(detail['articles']), 'linked articles, roles:', sorted({a['role'] for a in detail['articles']}))
    status, macro = call(base, token, 'GET', '/macro/current')
    assert status == 200 and macro['regions']['US']['inflation']['score'] > 0, macro
    print('macro/current US:', {k: f"{v['state']} {v['score']:+d}" for k, v in macro['regions']['US'].items() if v['score']})
    print('asset bias:', {a: d['score'] for a, d in macro['assets'].items() if d['score']})
    status, asset = call(base, token, 'GET', '/analysis/XAUUSD')
    assert status == 200 and asset['bias']['score'] < 0, asset
    status, brief = call(base, token, 'POST', '/briefs/daily')
    assert status == 200 and 'GLOBAL MACRO BRIEF' in brief['text'], brief
    print('daily brief rendered,', len(brief['text'].splitlines()), 'lines')
    status, post = call(base, token, 'GET', '/social/daily?platform=facebook')
    assert status == 200 and 'not trading advice' in post['text'] and '#Macro' in post['text'], post
    assert '<' not in post['text'], post
    print('social post ready,', post['chars'], 'chars,', ' '.join(post['hashtags']))
    status, result = call(base, token, 'POST', '/process/reactions')
    assert status == 200, result
    print('reactions:', result)
    status, result = call(base, token, 'GET', '/events/recent?hours=1')
    assert status == 200 and len(result) == 1, result


def main():
    env = read_env()
    container, base = start_engine(env)
    try:
        scenario(base, env['ENGINE_TOKEN'])
        print('SMOKE OK')
    finally:
        subprocess.run(['docker', 'stop', container], capture_output=True)
        sh('docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'eco', '-d', 'postgres', '-c', f'DROP DATABASE IF EXISTS {DB};',
           check=False)


if __name__ == '__main__':
    main()
