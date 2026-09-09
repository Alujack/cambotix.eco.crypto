#!/usr/bin/env python3
"""Pull the Ollama models named in .env (EXTRACT_MODEL, ANALYST_MODEL, EMBEDDINGS_MODEL) through the Ollama API.
Works for the native, docker (via published port is not needed: exec) and external runtimes."""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import read_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def wanted(env):
    models = []
    if env.get('AI_PROVIDER', 'ollama') == 'ollama':
        models += [env.get('EXTRACT_MODEL') or 'llama3.1:8b', env.get('ANALYST_MODEL') or 'llama3.1:8b']
    if env.get('EMBEDDINGS_PROVIDER', 'ollama') == 'ollama':
        models.append(env.get('EMBEDDINGS_MODEL') or 'nomic-embed-text')
    return list(dict.fromkeys(m for m in models if m and not m.startswith('claude-')))


def host_base(env):
    # From the host, the engine's container-side URL maps back to loopback.
    return (env.get('OLLAMA_BASE_URL') or 'http://127.0.0.1:11434').replace('host.docker.internal', '127.0.0.1').rstrip('/')


def pull_via_api(base, model):
    request = urllib.request.Request(base + '/api/pull', data=json.dumps({'model': model, 'stream': True}).encode(),
                                     headers={'Content-Type': 'application/json'})
    last = None
    with urllib.request.urlopen(request, timeout=600) as response:
        for line in response:
            event = json.loads(line)
            if event.get('error'):
                raise SystemExit(f"Ollama could not pull {model}: {event['error']}")
            text = event.get('status', '')
            if event.get('total'):
                text += f" {100 * event.get('completed', 0) // event['total'] // 10 * 10}%"
            if text != last:
                print(f'  {model}: {text}', flush=True)
                last = text


def main():
    env = read_env()
    models = wanted(env)
    if not models:
        print('No Ollama models configured.')
        return
    runtime = env.get('OLLAMA_RUNTIME', 'docker')
    for model in models:
        if runtime == 'docker':
            subprocess.run(['docker', 'compose', 'exec', '-T', 'ollama', 'ollama', 'pull', model], cwd=ROOT, check=True)
        else:
            base = host_base(env)
            try:
                pull_via_api(base, model)
            except (urllib.error.URLError, OSError) as error:
                raise SystemExit(f'Ollama at {base} is not reachable from the host ({error}).')
    print('Models available:', ', '.join(models))


if __name__ == '__main__':
    main()
