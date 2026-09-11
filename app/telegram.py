"""Telegram delivery through a Postgres outbox: enqueue -> send -> mark. Failures stay pending for the n8n flusher."""
import html
import logging
import time
from datetime import datetime, timezone

import httpx

from app import i18n, outlook, social
from app.config import env, output_language
from app.db import database

log = logging.getLogger('eco.telegram')
# Belt and braces: this module builds URLs containing the bot token, so silence httpx here too even when the app's
# logging setup in main.py has not run (tests, ad-hoc scripts).
logging.getLogger('httpx').setLevel(logging.WARNING)
CHUNK = 3900          # Telegram caps one message at 4096 UTF-16 code units; the margin absorbs the <pre> wrapper
CHUNK_PAUSE_SECONDS = 1.1   # Telegram: "avoid sending more than one message per second" to a single chat
MAX_ATTEMPTS = 5
# An alert is read on a phone, so it carries the assets this event actually moves hardest, not the whole universe.
ALERT_ASSETS = 4
RATIONALE_LIMIT = 260  # one or two sentences; a runaway rationale must not push the rest of the alert off the screen


class TelegramError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def configured() -> bool:
    return bool(env('TELEGRAM_BOT_TOKEN') and env('TELEGRAM_CHAT_ID'))


def channel_id() -> str:
    """The public channel the social posts go to. Separate from TELEGRAM_CHAT_ID on purpose: the operator's chat
    gets the digest plus one line of pipeline health, the channel gets the same read without the internals."""
    return env('TELEGRAM_CHANNEL_ID').strip()


def channel_configured() -> bool:
    return bool(env('TELEGRAM_BOT_TOKEN') and channel_id())


def width(text: str) -> int:
    """A message's length as Telegram measures it: UTF-16 code units, not characters.

    Every emoji outside the BMP counts two and a flag (a regional-indicator pair) counts four, so a brief full of
    🟢/🇺🇸 is longer to Telegram than len() says. Measuring in characters silently under-counts and can push a
    chunk past the 4096 cap, which fails the send rather than truncating it.
    """
    return len(text.encode('utf-16-le')) // 2


def _fits(text: str, limit: int) -> int:
    """Length of the longest prefix of `text` within `limit` UTF-16 units, counted in characters.

    Cutting on a character boundary can never split a surrogate pair, so an emoji is always kept whole.
    """
    if width(text) <= limit:
        return len(text)
    used = 0
    for index, char in enumerate(text):
        step = 2 if ord(char) > 0xFFFF else 1
        if used + step > limit:
            return index
        used += step
    return len(text)


def chunks(text: str, limit: int = CHUNK) -> list[str]:
    parts, current = [], ''
    for line in text.split('\n'):
        while width(line) > limit:
            cut = max(1, _fits(line, limit))   # max(1) so a limit narrower than one character cannot spin forever
            parts.append(line[:cut])
            line = line[cut:]
        candidate = line if not current else current + '\n' + line
        if width(candidate) > limit:
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


def send(text: str, parse_mode: str | None = None, target: str | None = None) -> None:
    """`target` is a chat or channel id; without one the message goes to the operator's TELEGRAM_CHAT_ID."""
    token, chat_id = env('TELEGRAM_BOT_TOKEN'), (target or env('TELEGRAM_CHAT_ID'))
    if not token or not chat_id:
        raise TelegramError('TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set', retryable=False)
    with httpx.Client(timeout=20) as client:
        for index, part in enumerate(render_chunks(text, parse_mode)):
            # Telegram asks senders to "avoid sending more than one message per second" to a single chat, and a
            # chunked message is exactly that burst: the old four-part brief went out back to back. Pace the parts
            # so a long message cannot earn a 429 that strands the rest of it half-delivered.
            if index:
                time.sleep(CHUNK_PAUSE_SECONDS)
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


def enqueue(conn, kind: str, ref_id: str, text: str, parse_mode: str | None = None,
            target: str | None = None) -> int | None:
    """A row with no target is delivered to TELEGRAM_CHAT_ID; a social post carries the channel it belongs to."""
    if not env('TELEGRAM_BOT_TOKEN') or not (target or env('TELEGRAM_CHAT_ID')):
        return None
    row = conn.execute('''INSERT INTO notifications (kind, ref_id, text, parse_mode, target)
                          VALUES (%s, %s, %s, %s, %s)
                          ON CONFLICT (kind, ref_id) DO UPDATE SET text = EXCLUDED.text, parse_mode = EXCLUDED.parse_mode,
                              target = EXCLUDED.target, status = 'pending', attempts = 0, error = NULL,
                              next_attempt_at = now()
                          RETURNING id''', (kind, ref_id, text, parse_mode, target or None)).fetchone()
    return row['id']


def deliver_pending(limit: int = 20, sender=send) -> dict:
    """Claim pending rows, send outside the transaction, record the outcome. Safe to call from several tickers."""
    with database() as conn:
        rows = conn.execute('''SELECT id, text, parse_mode, target, attempts FROM notifications
                               WHERE status = 'pending' AND next_attempt_at <= now()
                               ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED''', (limit,)).fetchall()
        if rows:
            conn.execute('UPDATE notifications SET attempts = attempts + 1 WHERE id = ANY(%s)', ([r['id'] for r in rows],))
    sent, failed = 0, 0
    for row in rows:
        try:
            sender(row['text'], row['parse_mode'], row['target'])
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
    return {'configured': configured(), 'chatId': env('TELEGRAM_CHAT_ID') or None,
            'channelId': channel_id() or None, 'pending': counts.get('pending', 0),
            'sent': counts.get('sent', 0), 'failed': counts.get('failed', 0),
            'lastSent': last['sent_at'] if last else None, 'lastKind': last['kind'] if last else None}


def format_event_alert(event: dict, analysis, article_count: int | None = None, lang: str | None = None) -> str:
    """HTML alert for one analyzed event. Everything user-derived is escaped; the title stays in the source's words."""
    lang = lang or output_language()
    L, esc = i18n.labels(lang), html.escape
    light = '🔴' if event['importance'] >= 90 else '🟠' if event['importance'] >= 80 else '🟡'
    # The economic read is worded once, in app.social, so the private alert and the public post cannot drift apart.
    reads = social.economic_read(analysis, lang, L)
    ranked = sorted(analysis.asset_impacts, key=lambda i: -abs(i.immediate.score))[:ALERT_ASSETS]
    # One translation call for the whole alert; each segment falls back to its English text on failure.
    chain_in = list(analysis.causal_chain[:5])
    prose = i18n.translate([analysis.summary] + chain_in + [impact.rationale for impact in ranked], lang)
    summary, chain, rationales = prose[0], prose[1:1 + len(chain_in)], prose[1 + len(chain_in):]
    lines = [f"{light} <b>{esc(event['title'])}</b>  ({L['importance']} {event['importance']})", '', esc(summary), '',
             esc(' · '.join(reads))]
    if ranked:
        # Per asset: which way, how the pressure travels, and the mechanism in the analyst's own words - a score on
        # its own ("BTC -45") tells a reader nothing they can act on.
        lines += ['', f"<b>{L['market_impact']}</b>"]
        for impact, rationale in zip(ranked, rationales):
            horizons = outlook.impact_horizons(impact)
            lines.append('• ' + esc(L['outlook_headline'].format(
                name=i18n.asset_name(impact.asset, lang), asset=impact.asset,
                direction=i18n.outlook_word(outlook.direction(horizons), lang),
                path=i18n.outlook_word(outlook.path(horizons), lang))))
            spread = L['alert_horizons'].format(now=f'{impact.immediate.score:+d}',
                                                week=f'{impact.short_term.score:+d}',
                                                months=f'{impact.medium_term.score:+d}')
            lines.append(f'   {esc(spread)} — {esc(outlook.clip(rationale, RATIONALE_LIMIT))}')
        lines.append('')
    if chain:
        lines += [f"<b>{L['chain']}:</b> " + esc(' → '.join(chain))]
    evidence = analysis.evidence_strength.lower() if lang == 'en' else i18n.enum(analysis.evidence_strength, lang)
    tail = [f"{L['confidence']} {analysis.confidence}",
            L['trend_relation'].format(relation=i18n.enum(analysis.relation_to_trend, lang)),
            L['evidence'].format(evidence=evidence)]
    if article_count:
        tail.append(L['source_items'].format(n=article_count))
    if i18n.translation_active(lang):
        tail.append(L['machine_translated'])
    lines += ['', esc(' · '.join(tail)),
              esc(L['event_tail'].format(id=event['id'], stamp=f'{datetime.now(timezone.utc):%Y-%m-%d %H:%M}'))]
    return '\n'.join(lines)
