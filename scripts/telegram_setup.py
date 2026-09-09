#!/usr/bin/env python3
"""Connect the engine to a Telegram bot: verify the token, discover the chat id from the bot's updates (send /start to
the bot first, or add it to a group), write TELEGRAM_CHAT_ID to .env, restart the engine and send a test message.
Usage: python3 scripts/telegram_setup.py [--chat-id ID]"""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from setup import read_env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def api(token, method):
    with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/{method}', timeout=20) as response:
        return json.load(response)


def discover_chats(token) -> dict:
    chats = {}
    for update in api(token, 'getUpdates').get('result', []):
        for key in ('message', 'channel_post', 'my_chat_member', 'edited_message'):
            chat = (update.get(key) or {}).get('chat')
            if chat:
                chats[str(chat['id'])] = f"{chat.get('type')}: {chat.get('title') or chat.get('username') or chat.get('first_name')}"
    return chats


def write_chat_id(chat_id: str) -> None:
    env_path = ROOT / '.env'
    text = env_path.read_text()
    if re.search(r'^TELEGRAM_CHAT_ID=.*$', text, re.M):
        text = re.sub(r'^TELEGRAM_CHAT_ID=.*$', f'TELEGRAM_CHAT_ID={chat_id}', text, flags=re.M)
    else:
        text = text.rstrip('\n') + f'\nTELEGRAM_CHAT_ID={chat_id}\n'
    env_path.write_text(text, newline='\n')
    env_path.chmod(0o600)


def main():
    env = read_env()
    token = env.get('TELEGRAM_BOT_TOKEN')
    if not token:
        raise SystemExit('Set TELEGRAM_BOT_TOKEN in .env first (from @BotFather).')
    try:
        me = api(token, 'getMe')
    except urllib.error.HTTPError as error:
        raise SystemExit(f'Telegram rejected the token ({error.code}). Check TELEGRAM_BOT_TOKEN.')
    bot = me['result']['username']
    print(f'Bot: @{bot}')
    chat_id = sys.argv[sys.argv.index('--chat-id') + 1] if '--chat-id' in sys.argv else None
    if not chat_id:
        chats = discover_chats(token)
        if not chats:
            raise SystemExit(f'No chat yet. Open https://t.me/{bot}, send /start (or add the bot to a group), then rerun.')
        if len(chats) > 1:
            for cid, label in chats.items():
                print(f'  {cid:<16} {label}')
            raise SystemExit('Several chats seen; rerun with --chat-id <id>.')
        chat_id, label = next(iter(chats.items()))
        print(f'Chat: {chat_id} ({label})')
    write_chat_id(chat_id)
    subprocess.run(['docker', 'compose', 'up', '-d', '--wait', 'engine'], cwd=ROOT, check=True, capture_output=True)
    env = read_env()
    request = urllib.request.Request(f"http://127.0.0.1:{env.get('ENGINE_PORT', '8020')}/notify/test", method='POST',
                                     headers={'X-Eco-Token': env['ENGINE_TOKEN']})
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result.get('sent'):
        print('Test message delivered. Daily brief: 06:00 UTC; event alerts: importance >= '
              f"{env.get('TELEGRAM_ALERT_MIN_IMPORTANCE', '80')}.")
    else:
        raise SystemExit(f'Delivery failed: {result}. Check `docker compose logs engine`.')


if __name__ == '__main__':
    main()
