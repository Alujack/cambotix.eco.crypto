#!/usr/bin/env python3
"""Quick health view: engine, AI configuration, queue depths, macro state and the newest events."""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import read_env  # noqa: E402


def get(base, token, path):
    request = urllib.request.Request(base + path, headers={'X-Eco-Token': token})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main():
    env = read_env()
    base = f"http://127.0.0.1:{env.get('ENGINE_PORT', '8020')}"
    try:
        health = get(base, env['ENGINE_TOKEN'], '/health')
    except (urllib.error.URLError, OSError) as error:
        raise SystemExit(f'Engine not reachable at {base}: {error}')
    ai = health['ai']
    print(f"Engine        {base}  ok   trades={health['trades']}")
    print(f"AI            {ai['provider']}  {'configured' if ai['configured'] else 'NOT CONFIGURED (set ANTHROPIC_API_KEY)'}"
          f"  extract={ai['extractModel']}  analyst={ai['analystModel']}")
    print(f"Embeddings    {health['embeddings']['provider']}  {health['embeddings']['model'] or ''}")
    print(f"n8n editor    http://localhost:{env.get('N8N_PORT', '5681')}")
    tg = get(base, env['ENGINE_TOKEN'], '/notify/status')
    if tg['configured']:
        print(f"Telegram      chat {tg['chatId']}  sent={tg['sent']} pending={tg['pending']} failed={tg['failed']}"
              f"  last={tg['lastKind'] or '-'} {tg['lastSent'] or ''}")
    else:
        print('Telegram      not connected (python3 scripts/telegram_setup.py)')
    sources = get(base, env['ENGINE_TOKEN'], '/sources')
    active = [s for s in sources if s['articles']]
    print(f"Sources       {len(sources)} registered, {len(active)} delivering, "
          f"{sum(s['articles'] for s in sources)} articles stored")
    macro = get(base, env['ENGINE_TOKEN'], '/macro/current')
    print(f"Macro         risk={macro['riskRegime']}  liquidity={macro['globalLiquidity']}  "
          f"geopolitics={macro['geopoliticalRisk']}")
    print('Asset bias    ' + '  '.join(f"{asset} {data['score']:+d}" for asset, data in macro['assets'].items()))
    events = get(base, env['ENGINE_TOKEN'], '/events/recent?hours=48&limit=8')
    print(f"Events (48h)  {len(events)} shown")
    for event in events:
        flag = 'analyzed' if event['summary'] else event['status']
        print(f"  [{event['importance']:>3}] {flag:<9} {event['title'][:70]}")
        if event['summary']:
            print(f"        {event['summary'][:110]}")


if __name__ == '__main__':
    main()
