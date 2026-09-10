#!/usr/bin/env python3
"""Re-analyse the same events with different local models and report measured quality, not impressions.

    python3 scripts/compare_models.py llama3.1:8b qwen2.5:7b [--events 4]

For each model it rewrites EXTRACT_MODEL/ANALYST_MODEL in .env, restarts the engine, forces a fresh analysis of the
same sample of real events, and records consistency flags, assets scored and wall-clock time. The original .env values
are restored at the end (including after Ctrl-C).

Both model variables are set to the same id on purpose: this host has ~1.5 GB of RAM free, so two resident models
would thrash. The analyst is what is being measured; extraction does not run during a forced re-analysis.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import read_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
KEYS = ('EXTRACT_MODEL', 'ANALYST_MODEL')
# The n8n schedulers analyse the live queue every 30-60s. Left running they take the engine's inference lock and
# analyse unrelated events with whichever model is configured, which contaminates a comparison. Paused for the run.
SCHEDULERS = ('eco08Extract', 'eco09Analyze')


def api(path, token, method='GET', timeout=1800):
    env = read_env()
    request = urllib.request.Request(f"http://127.0.0.1:{env.get('ENGINE_PORT', '8020')}{path}", method=method,
                                     headers={'X-Eco-Token': token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def set_schedulers(active: bool) -> None:
    for workflow in SCHEDULERS:
        subprocess.run(['docker', 'compose', 'exec', '-T', 'n8n', 'n8n', 'update:workflow', f'--id={workflow}',
                        f'--active={"true" if active else "false"}'], cwd=ROOT, capture_output=True)
    print(f'n8n schedulers {"resumed" if active else "paused"}.', flush=True)


def set_models(model: str) -> None:
    path = ROOT / '.env'
    text = path.read_text()
    for key in KEYS:
        text = (re.sub(rf'^{key}=.*$', f'{key}={model}', text, flags=re.M) if re.search(rf'^{key}=', text, re.M)
                else text.rstrip('\n') + f'\n{key}={model}\n')
    path.write_text(text)
    path.chmod(0o600)
    subprocess.run(['docker', 'compose', 'up', '-d', '--wait', 'engine'], cwd=ROOT, check=True, capture_output=True)


def sample_events(token, count) -> list[str]:
    rows = api(f'/events/recent?hours=168&limit={count * 3}', token)
    return [row['id'] for row in rows if row.get('status') == 'analyzed'][:count]


def reanalyze(token, event_id) -> dict:
    subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-U', 'eco', '-d', 'eco', '-tAc',
                    "UPDATE economic_events SET needs_analysis=true, status='open', next_attempt_at=now() "
                    f"WHERE id='{event_id}'"], cwd=ROOT, check=True, capture_output=True)
    started = time.monotonic()
    try:
        result = api('/process/analyze?force=true', token, method='POST')
    except (urllib.error.URLError, TimeoutError) as error:
        return {'event': event_id, 'error': str(error)[:60], 'seconds': time.monotonic() - started}
    if result.get('analyzed') != 1:
        return {'event': event_id, 'error': result.get('skipped') or result.get('reason') or 'not analyzed',
                'seconds': time.monotonic() - started}
    flags = result.get('consistencyFlags') or []
    return {'event': result['eventId'], 'assets': result['assetImpacts'], 'flags': len(flags),
            'codes': sorted({f['code'] for f in flags}), 'seconds': time.monotonic() - started}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('models', nargs='+')
    parser.add_argument('--events', type=int, default=4)
    args = parser.parse_args()
    env = read_env()
    token, original = env['ENGINE_TOKEN'], env.get('ANALYST_MODEL', 'llama3.1:8b')
    events = sample_events(token, args.events)
    if not events:
        raise SystemExit('No analysed events to re-run. Let the pipeline analyse a few first.')
    print(f'Comparing on {len(events)} event(s): {", ".join(events)}\n', flush=True)
    results = {}
    set_schedulers(False)
    try:
        for model in args.models:
            print(f'--- {model} ---', flush=True)
            set_models(model)
            rows = []
            for event_id in events:
                row = reanalyze(token, event_id)
                rows.append(row)
                detail = row.get('error') or f"{row['assets']} assets, {row['flags']} flags {row['codes']}"
                print(f"  {row['event']}  {row['seconds']:6.1f}s  {detail}", flush=True)
            results[model] = rows
    finally:
        set_models(original)
        set_schedulers(True)
        print(f'Restored EXTRACT_MODEL/ANALYST_MODEL to {original}.', flush=True)
    print(f"\n{'model':<22}{'analyses':>9}{'flags/analysis':>16}{'assets/analysis':>17}{'sec/analysis':>14}",
          flush=True)
    for model, rows in results.items():
        ok = [r for r in rows if 'error' not in r]
        if not ok:
            print(f'{model:<22}{"all failed":>9}')
            continue
        print(f"{model:<22}{len(ok):>9}{sum(r['flags'] for r in ok) / len(ok):>16.2f}"
              f"{sum(r['assets'] for r in ok) / len(ok):>17.2f}{sum(r['seconds'] for r in ok) / len(ok):>14.1f}")
    print('\nFewer flags is better; assets/analysis shows whether a model commits to scores at all.')


if __name__ == '__main__':
    main()
