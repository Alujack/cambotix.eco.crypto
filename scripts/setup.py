#!/usr/bin/env python3
"""Generate local secrets and the n8n imports (credential + workflows) from sources/registry.json.
Never overwrites an existing .env. The committed n8n/*.json templates are regenerated on every run."""
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = 'http://engine:8000'
CREDENTIAL = {'id': 'ecoEngineAuth', 'name': 'Eco engine internal API'}
# workflow group -> (id, display name, poll interval in minutes)
COLLECTORS = {
    'central_banks': ('eco01CentralBanks', 'Eco 01 — Central banks', 5),
    'official_data': ('eco02OfficialData', 'Eco 02 — Official statistics', 5),
    'regulators': ('eco03Regulators', 'Eco 03 — Regulators (SEC / CFTC)', 10),
    'financial_news': ('eco04FinancialNews', 'Eco 04 — Financial news', 15),
    'crypto_news': ('eco05CryptoNews', 'Eco 05 — Crypto news', 5),
}
RSS_NORMALIZE_JS = r'''// One run per upstream feed node; $prevNode.name is the source key from sources/registry.json.
const source = $prevNode.name;
const strip = (s) => String(s || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
const items = [];
for (const item of $input.all()) {
  const j = item.json || {};
  if (!j.title || j.error) continue;
  items.push({
    headline: strip(j.title).slice(0, 500),
    url: j.link || null,
    externalId: j.guid || j.id || j.link || null,
    publishedAt: j.isoDate || j.pubDate || j.published || null,
    content: strip(j.contentSnippet || j.content || j.summary || j.description).slice(0, 4000),
    publisher: j.creator || j.author || null,
  });
}
if (!items.length) return [];
return [{ json: { source, items } }];
'''
ALPHA_VANTAGE_JS = r'''// Alpha Vantage NEWS_SENTIMENT -> canonical batch. A rate-limit body has no `feed`, so the branch just stops.
const j = $input.first().json || {};
const feed = Array.isArray(j.feed) ? j.feed : [];
const items = feed.map(a => ({
  headline: String(a.title || '').slice(0, 500),
  url: a.url || null,
  externalId: a.url || null,
  publishedAt: a.time_published || null,
  content: String(a.summary || '').slice(0, 4000),
  publisher: a.source || null,
  topics: (a.topics || []).map(t => t.topic),
})).filter(a => a.headline);
if (!items.length) return [];
return [{ json: { source: 'alphavantage_news', items } }];
'''
CALENDAR_JS = r'''// ForexFactory weekly JSON -> canonical calendar batch. Times carry an offset; the engine converts to UTC.
const items = [];
for (const item of $input.all()) {
  const r = item.json || {};
  if (!r.title || !r.date) continue;
  items.push({
    title: String(r.title).slice(0, 300),
    currency: String(r.country || '').toUpperCase(),
    scheduledAt: r.date,
    impact: r.impact || '',
    forecast: r.forecast || null,
    previous: r.previous || null,
    actual: r.actual || null,
  });
}
if (!items.length) return [];
return [{ json: { source: 'forexfactory_calendar', items } }];
'''


def write(path: Path, text: str) -> None:
    path.write_text(text, newline='\n')


def read_env() -> dict:
    result = {}
    for line in (ROOT / '.env').read_text().splitlines():
        if line.strip() and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip()
    return result


def node(name, kind, parameters, x, y=0, version=1, **kwargs):
    return {'id': name.lower().replace(' ', '-').replace('/', '-'), 'name': name, 'type': 'n8n-nodes-base.' + kind,
            'typeVersion': version, 'position': [x, y], 'parameters': parameters, **kwargs}


def schedule(minutes=None, seconds=None, cron=None):
    if cron:
        rule = {'interval': [{'field': 'cronExpression', 'expression': cron}]}
    elif seconds:
        rule = {'interval': [{'field': 'seconds', 'secondsInterval': seconds}]}
    else:
        rule = {'interval': [{'field': 'minutes', 'minutesInterval': minutes}]}
    label = f'Every {seconds} seconds' if seconds else f'Every {minutes} minutes' if minutes else 'Daily 06:00 UTC'
    return node(label, 'scheduleTrigger', {'rule': rule}, 0, version=1.2)


def engine_post(name, path, x, y=0, timeout=30000, body=None):
    parameters = {'method': 'POST', 'url': ENGINE + path, 'authentication': 'genericCredentialType',
                  'genericAuthType': 'httpHeaderAuth', 'options': {'timeout': timeout}}
    if body:
        parameters.update({'sendBody': True, 'specifyBody': 'json', 'jsonBody': body})
    return node(name, 'httpRequest', parameters, x, y, version=4.2, credentials={'httpHeaderAuth': CREDENTIAL})


def code(name, js, x, y=0):
    return node(name, 'code', {'jsCode': js}, x, y, version=2)


def chain(nodes):
    return {a['name']: {'main': [[{'node': b['name'], 'type': 'main', 'index': 0}]]} for a, b in zip(nodes, nodes[1:])}


def workflow(identifier, name, nodes, connections):
    return {'id': identifier, 'name': name, 'active': False, 'nodes': nodes, 'connections': connections,
            'settings': {'executionOrder': 'v1', 'timezone': 'UTC'}, 'pinData': {}, 'tags': []}


def collector_workflow(identifier, name, minutes, feeds):
    trigger = schedule(minutes=minutes)
    normalize = code('Normalize feed items', RSS_NORMALIZE_JS, 560, (len(feeds) - 1) * 100)
    post = engine_post('Ingest into engine', '/ingest/articles', 820, (len(feeds) - 1) * 100, body='={{ JSON.stringify($json) }}')
    feed_nodes = [node(feed['key'], 'rssFeedRead', {'url': feed['url'], 'options': {}}, 280, i * 200, version=1.1,
                       onError='continueRegularOutput') for i, feed in enumerate(feeds)]
    connections = {trigger['name']: {'main': [[{'node': f['name'], 'type': 'main', 'index': 0} for f in feed_nodes]]}}
    for feed_node in feed_nodes:
        connections[feed_node['name']] = {'main': [[{'node': normalize['name'], 'type': 'main', 'index': 0}]]}
    connections.update(chain([normalize, post]))
    return workflow(identifier, name, [trigger, *feed_nodes, normalize, post], connections)


def build_workflows(registry: dict) -> list[dict]:
    workflows = []
    for group, (identifier, name, minutes) in COLLECTORS.items():
        feeds = [s for s in registry['sources'] if s.get('workflow') == group and s['type'] == 'rss' and s.get('enabled', True)]
        if feeds:
            workflows.append(collector_workflow(identifier, name, minutes, feeds))
    alpha = schedule(minutes=60)
    alpha_http = node('Alpha Vantage NEWS_SENTIMENT', 'httpRequest', {
        'url': 'https://www.alphavantage.co/query', 'sendQuery': True,
        'queryParameters': {'parameters': [
            {'name': 'function', 'value': 'NEWS_SENTIMENT'},
            {'name': 'topics', 'value': 'economy_macro,economy_monetary,economy_fiscal,blockchain'},
            {'name': 'sort', 'value': 'LATEST'}, {'name': 'limit', 'value': '50'},
            {'name': 'apikey', 'value': '={{ $env.ALPHA_VANTAGE_API_KEY }}'}]},
        'options': {'timeout': 30000, 'response': {'response': {'responseFormat': 'json'}}}}, 280, version=4.2,
        onError='continueRegularOutput')
    alpha_code = code('Normalize Alpha Vantage', ALPHA_VANTAGE_JS, 560)
    alpha_post = engine_post('Ingest into engine', '/ingest/articles', 820, body='={{ JSON.stringify($json) }}')
    nodes = [alpha, alpha_http, alpha_code, alpha_post]
    workflows.append(workflow('eco06AlphaVantage', 'Eco 06 — Alpha Vantage news (hourly, 25/day quota)', nodes, chain(nodes)))

    cal = schedule(minutes=60)
    cal_http = node('ForexFactory weekly calendar', 'httpRequest', {
        'url': 'https://nfs.faireconomy.media/ff_calendar_thisweek.json',
        'options': {'timeout': 30000, 'response': {'response': {'responseFormat': 'json'}}}}, 280, version=4.2)
    cal_code = code('Normalize calendar', CALENDAR_JS, 560)
    cal_post = engine_post('Ingest calendar', '/ingest/calendar', 820, body='={{ JSON.stringify($json) }}')
    nodes = [cal, cal_http, cal_code, cal_post]
    workflows.append(workflow('eco07Calendar', 'Eco 07 — Economic calendar', nodes, chain(nodes)))

    for identifier, name, trig, path, timeout in (
            ('eco08Extract', 'Eco 08 — Extract facts + cluster events', schedule(seconds=30), '/process/extract', 240000),
            ('eco09Analyze', 'Eco 09 — Economic analyst', schedule(seconds=60), '/process/analyze', 400000),
            ('eco10Reactions', 'Eco 10 — Market reaction tracker', schedule(minutes=5), '/process/reactions', 60000),
            ('eco11DailyBrief', 'Eco 11 — Daily macro brief', schedule(cron='0 6 * * *'), '/briefs/daily', 400000)):
        nodes = [trig, engine_post('Call engine ' + path, path, 280, timeout=timeout)]
        workflows.append(workflow(identifier, name, nodes, chain(nodes)))
    return workflows


def main():
    env_path = ROOT / '.env'
    if not env_path.exists():
        content = (ROOT / '.env.example').read_text()
        for key in ('POSTGRES_PASSWORD', 'N8N_ENCRYPTION_KEY', 'ENGINE_TOKEN'):
            content = content.replace(f'{key}=GENERATE', f'{key}={secrets.token_hex(32)}')
        write(env_path, content)
    env_path.chmod(0o600)
    env = read_env()
    if any(not env.get(key) or env[key] == 'GENERATE' for key in ('POSTGRES_PASSWORD', 'N8N_ENCRYPTION_KEY', 'ENGINE_TOKEN')):
        raise SystemExit('Fill the required secrets in .env; an existing .env is never overwritten.')
    if env.get('AI_PROVIDER', 'anthropic') == 'anthropic' and not (env.get('ANTHROPIC_API_KEY') or env.get('ANTHROPIC_AUTH_TOKEN')):
        print('Note: ANTHROPIC_API_KEY is empty. Collection works; extraction/analysis return 503 until it is set '
              '(or AI_PROVIDER=mock).', file=sys.stderr)
    registry = json.loads((ROOT / 'sources' / 'registry.json').read_text())
    workflows = build_workflows(registry)
    local = ROOT / '.local'
    local.mkdir(exist_ok=True, mode=0o700)
    imports = local / 'import'
    imports.mkdir(exist_ok=True)
    write(imports / 'workflows.json', json.dumps(workflows, indent=2) + '\n')
    credential_path = imports / 'credentials.json'
    write(credential_path, json.dumps([{**CREDENTIAL, 'type': 'httpHeaderAuth',
                                        'data': {'name': 'X-Eco-Token', 'value': env['ENGINE_TOKEN']}}]))
    credential_path.chmod(0o600)
    write(local / 'workflow-ids.txt', '\n'.join(w['id'] for w in workflows) + '\n')
    for item in workflows:
        write(ROOT / 'n8n' / (item['id'] + '.json'), json.dumps(item, indent=2) + '\n')
    print(f'Prepared .env, private imports and {len(workflows)} n8n workflow templates.')


if __name__ == '__main__':
    main()
