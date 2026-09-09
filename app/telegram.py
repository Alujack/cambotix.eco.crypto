"""Telegram delivery through a Postgres outbox: enqueue -> send -> mark. Failures stay pending for the n8n flusher."""
import html
import logging
from datetime import datetime, timezone

import httpx

from app import i18n
from app.config import env, output_language
from app.db import database

log = logging.getLogger('eco.telegram')
# Belt and braces: this module builds URLs containing the bot token, so silence httpx here too even when the app's
# logging setup in main.py has not run (tests, ad-hoc scripts).
logging.getLogger('httpx').setLevel(logging.WARNING)
CHUNK = 3900  # Telegram caps a message at 4096 characters
MAX_ATTEMPTS = 5


class TelegramError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def configured() -> bool:
    return bool(env('TELEGRAM_BOT_TOKEN') and env('TELEGRAM_CHAT_ID'))


def chunks(text: str, limit: int = CHUNK) -> list[str]:
    parts, current = [], ''
    for line in text.split('\n'):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        candidate = line if not current else current + '\n' + line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts or ['']


def render_chunks(text: str, parse_mode: str | None) -> list[str]:
    """HTML_PRE = fixed-width block (tables stay aligned in Telegram); each chunk is escaped and wrapped on its own."""
    if parse_mode == 'HTML_PRE':
        # Escape first: entities widen the text, and they never contain newlines, so line-based chunking stays safe.
        return [f'<pre>{part}</pre>' for part in chunks(html.escape(text), CHUNK - 11)]
    return chunks(text)


def send(text: str, parse_mode: str | None = None) -> None:
    token, chat_id = env('TELEGRAM_BOT_TOKEN'), env('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        raise TelegramError('TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set', retryable=False)
    with httpx.Client(timeout=20) as client:
        for part in render_chunks(text, parse_mode):
            payload = {'chat_id': chat_id, 'text': part, 'disable_web_page_preview': True}
            if parse_mode:
                payload['parse_mode'] = 'HTML' if parse_mode == 'HTML_PRE' else parse_mode
            try:
                response = client.post(f'https://api.telegram.org/bot{token}/sendMessage', json=payload)
            except httpx.HTTPError as error:
                raise TelegramError(f'network: {error}') from error
            if response.status_code == 429:
                raise TelegramError('rate limited by Telegram')
            body = response.json() if response.headers.get('content-type', '').startswith('application/json') else {}
            if response.status_code >= 400 or not body.get('ok'):
                description = body.get('description') or response.text[:200]
                raise TelegramError(f'telegram {response.status_code}: {description}', retryable=response.status_code >= 500)


def enqueue(conn, kind: str, ref_id: str, text: str, parse_mode: str | None = None) -> int | None:
    if not configured():
        return None
    row = conn.execute('''INSERT INTO notifications (kind, ref_id, text, parse_mode)
                          VALUES (%s, %s, %s, %s)
                          ON CONFLICT (kind, ref_id) DO UPDATE SET text = EXCLUDED.text, parse_mode = EXCLUDED.parse_mode,
                              status = 'pending', attempts = 0, error = NULL, next_attempt_at = now()
                          RETURNING id''', (kind, ref_id, text, parse_mode)).fetchone()
    return row['id']


def deliver_pending(limit: int = 20, sender=send) -> dict:
    """Claim pending rows, send outside the transaction, record the outcome. Safe to call from several tickers."""
    with database() as conn:
        rows = conn.execute('''SELECT id, text, parse_mode, attempts FROM notifications
                               WHERE status = 'pending' AND next_attempt_at <= now()
                               ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED''', (limit,)).fetchall()
        if rows:
            conn.execute('UPDATE notifications SET attempts = attempts + 1 WHERE id = ANY(%s)', ([r['id'] for r in rows],))
    sent, failed = 0, 0
    for row in rows:
        try:
            sender(row['text'], row['parse_mode'])
        except TelegramError as error:
            failed += 1
            attempts = row['attempts'] + 1
            final = not error.retryable or attempts >= MAX_ATTEMPTS
            log.warning('notification %s failed (%s/%s): %s', row['id'], attempts, MAX_ATTEMPTS, error)
            with database() as conn:
                conn.execute('''UPDATE notifications SET status = %s, error = %s,
                                next_attempt_at = now() + make_interval(secs => %s) WHERE id = %s''',
                             ('failed' if final else 'pending', str(error)[:300], 30 * attempts ** 2, row['id']))
            continue
        sent += 1
        with database() as conn:
            conn.execute("UPDATE notifications SET status = 'sent', sent_at = now(), error = NULL WHERE id = %s", (row['id'],))
    return {'claimed': len(rows), 'sent': sent, 'failed': failed}


def status(conn) -> dict:
    counts = {row['status']: row['n'] for row in conn.execute('SELECT status, count(*) AS n FROM notifications GROUP BY status')}
    last = conn.execute('SELECT kind, sent_at FROM notifications WHERE status = %s ORDER BY sent_at DESC LIMIT 1', ('sent',)).fetchone()
    return {'configured': configured(), 'chatId': env('TELEGRAM_CHAT_ID') or None, 'pending': counts.get('pending', 0),
            'sent': counts.get('sent', 0), 'failed': counts.get('failed', 0),
            'lastSent': last['sent_at'] if last else None, 'lastKind': last['kind'] if last else None}


def format_event_alert(event: dict, analysis, article_count: int | None = None, lang: str | None = None) -> str:
    """HTML alert for one analyzed event. Everything user-derived is escaped; the title stays in the source's words."""
    lang = lang or output_language()
    L, esc = i18n.labels(lang), html.escape
    light = '🔴' if event['importance'] >= 90 else '🟠' if event['importance'] >= 80 else '🟡'
    interp = analysis.economic_interpretation
    cb = analysis.central_bank_implication
    reads = [f"{L['read_inflation']} {i18n.enum(interp.inflation, lang)}",
             f"{L['read_growth']} {i18n.enum(interp.growth, lang)}",
             f"{L['read_liquidity']} {i18n.enum(interp.liquidity, lang)}"]
    if cb.fed != 'NOT_RELEVANT':
        reads.append(f'Fed {i18n.enum(cb.fed, lang)}')
    if cb.ecb != 'NOT_RELEVANT':
        reads.append(f'ECB {i18n.enum(cb.ecb, lang)}')
    reads.append(f"{L['read_risk']} {i18n.enum(analysis.risk_regime_impact, lang)}")
    impacts = ' · '.join(f'{i.asset} {i.immediate.score:+d}/{i.short_term.score:+d}'
                         for i in sorted(analysis.asset_impacts, key=lambda i: -abs(i.immediate.score))[:6])
    # One translation call for the whole alert; each segment falls back to its English text on failure.
    prose = i18n.translate([analysis.summary] + list(analysis.causal_chain[:5]), lang)
    summary, chain = prose[0], prose[1:]
    lines = [f"{light} <b>{esc(event['title'])}</b>  ({L['importance']} {event['importance']})", '', esc(summary), '',
             esc(' · '.join(reads))]
    if impacts:
        lines += [f"<b>{L['impact_now']}:</b> {esc(impacts)}"]
    if chain:
        lines += [f"<b>{L['chain']}:</b> " + esc(' → '.join(chain))]
    relation = (analysis.relation_to_trend.lower().replace('_', ' ') if lang == 'en'
                else i18n.enum(analysis.relation_to_trend, lang))
    evidence = analysis.evidence_strength.lower() if lang == 'en' else i18n.enum(analysis.evidence_strength, lang)
    tail = [f"{L['confidence']} {analysis.confidence}", L['trend_relation'].format(relation=relation),
            L['evidence'].format(evidence=evidence)]
    if article_count:
        tail.append(L['source_items'].format(n=article_count))
    if i18n.translation_active(lang):
        tail.append(L['machine_translated'])
    lines += ['', esc(' · '.join(tail)),
              esc(L['event_tail'].format(id=event['id'], stamp=f'{datetime.now(timezone.utc):%Y-%m-%d %H:%M}'))]
    return '\n'.join(lines)
