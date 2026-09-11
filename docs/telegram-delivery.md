# Telegram delivery

How the engine's output reaches a phone, why the daily brief is a one-message digest, and the platform limits
that shape both. Decided 2026-09-11 after the delivered brief was measured at four messages and judged unreadable.

## The problem, measured

The daily brief as rendered by `app/briefs.render` was **12,617 characters**. `app/telegram.chunks` split it into
**four separate Telegram messages**, sent back to back:

```
EN brief (HTML_PRE): 12608 chars / 12624 utf-16  ->  4 messages
    msg 1: 3885 chars  3901 utf-16
    msg 2: 3891 chars  3891 utf-16
    msg 3: 3817 chars  3817 utf-16
    msg 4: 1260 chars  1260 utf-16
KM brief as actually sent: 12617 chars / 12633 utf-16  ->  4 messages
```

Beyond the length, the order was wrong for a reader. The first message opened with pipeline counters, a
self-consistency warning quoting an API path (`GET /analysis-quality`), and fifteen rows of macro state. The actual
market read — the reason the brief exists — began partway through message two. Message four was headline lists.

## The decision

**Telegram receives one message: the digest. The full brief stays stored and served.**

The operator's chat now gets `brief['operatorPost']` — the same news-order layout `app/social.py` already produced
for the public channel, plus one line of pipeline health that a public reader does not need:

| | Full brief | Digest (`operatorPost`) | Public post |
|---|---|---|---|
| Characters | ~12,600 | ~2,400 | ~2,300 |
| Telegram messages | 4 | 1 | 1 |
| Macro dimensions | all 15 | top 3 | top 3 |
| Market reads | 6, verbose | 3, one reason each | 3 |
| Headline lists | 14 rows | none | none |
| Pipeline counters | 2-line header | one `⚙` line | none |

Nothing is lost. The complete text is still written to the `briefs` table and served by `GET /briefs/latest`
(`?format=text` for the plain render). The digest is what the phone receives, not what the engine keeps.

`TELEGRAM_BRIEF_STYLE=full` in `.env` restores the old four-message body if the full text is ever wanted on the
phone. Default is `digest` — see `app/config.brief_style`.

## Platform limits that shape this

### Message length is 4096 **UTF-16 code units**, not characters

This is the subtle one, and the previous code got it wrong. Telegram counts length in UTF-16 code units:

> the number of UTF-16 code units, even if the message itself must be encoded using UTF-8
>
> - Code points in the BMP (`U+0000` to `U+FFFF`) count as 1
> - Code points in all other planes count as 2
>
> — [Telegram: entity length](https://core.telegram.org/api/entities#entity-length)

So in a brief full of status lamps and flags:

| Text | `len()` | Telegram's count |
|---|---|---|
| `abc` | 3 | 3 |
| `🟢` | 1 | **2** |
| `🇺🇸` (regional-indicator pair) | 2 | **4** |
| Khmer `អតិផរណា` (all BMP) | 7 | 7 |

Chunking on `len()` under-counts an emoji-dense message, and exceeding the cap **fails the send** — Telegram
rejects the request rather than truncating. Khmer itself is safe (the whole block `U+1780`–`U+17FF` is BMP), so
the exposure comes entirely from emoji, and the brief header alone carries fifteen lamps plus flags.

`app/telegram.width` now measures the way Telegram does, and `app/telegram._fits` cuts on a character boundary so
a surrogate pair is never split in half. Measured headroom before the fix was thin but not yet breached — the
worst chunk was 3901 units against the 3900-character budget.

### One message per second, per chat

> **Private chats:** avoid sending more than one message per second
> **Groups:** bots are not be able to send more than 20 messages per minute
> **Bulk notifications:** bots are not able to broadcast more than about 30 messages per second
>
> — [Telegram Bot FAQ](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this)

The old four-part brief was sent in a tight loop with no delay — exactly the burst this warns against. A 429 part
way through leaves the message half-delivered, since `app/telegram.send` raises on the first failing chunk and the
outbox retries the whole row. `CHUNK_PAUSE_SECONDS = 1.1` now paces chunks. The digest makes this mostly moot for
the brief, but event alerts can still chunk.

### `CHUNK = 3900`, not 4096

The margin absorbs the `<pre>` wrapper added per chunk in `HTML_PRE` mode (11 units) and the widening from HTML
escaping (`&` → `&amp;`). Escaping happens *before* chunking in `render_chunks`, so the measured width is the
width actually sent.

## Where each thing is rendered

| Output | Built by | Delivered to | Parse mode |
|---|---|---|---|
| Full brief | `app/briefs.render` | `briefs` table, `GET /briefs/latest` | `HTML_PRE` (en) / plain (km) |
| Operator digest | `app/social.daily_post(operations=True)` | `TELEGRAM_CHAT_ID` | `HTML` |
| Public daily post | `app/social.daily_post` | `TELEGRAM_CHANNEL_ID`, `GET /social/daily` | `HTML` / plain (facebook) |
| Event alert | `app/telegram.format_event_alert` | `TELEGRAM_CHAT_ID` above `TELEGRAM_ALERT_MIN_IMPORTANCE` | `HTML` |
| Public event post | `app/social.event_post` | `TELEGRAM_CHANNEL_ID` above `SOCIAL_MIN_IMPORTANCE` | `HTML` / plain |

`HTML_PRE` is a local convention, not a Telegram parse mode: it means "escape, then wrap each chunk in `<pre>`",
sent with `parse_mode=HTML`. It exists so the full brief's fixed-width columns stay aligned. The digest does not
use it — proportional text does not need a character grid, and `<pre>` would fight the layout.

Telegram's supported formatting is deliberately narrow (`<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<a>`, and
a few more); anything else is rejected. See
[formatting options](https://core.telegram.org/bots/api#formatting-options). This is why `app/social.PLATFORMS`
escapes everything user-derived and why `facebook` renders the identical words with no tags at all.

## Still open

- **Khmer delivery is still part English.** `OUTPUT_LANGUAGE=km` translates the *labels* from the static tables in
  `app/i18n.py`, but prose — the lede, each market's reason, the risk bullets — needs one `TRANSLATE_MODEL` call
  and falls back to English without `ANTHROPIC_API_KEY`. The digest fixed the length and the ordering; it did not
  fix this. `/health` reports the state as `delivery.translatesProse`. Local 8B models are not good enough at
  Khmer to publish, so this needs the key.
- **Event alerts are already fine** — measured 2026-09-11 across the five highest-importance analyses on record:
  1,159–1,771 UTF-16 units, one message each, against a 3,900 budget. They are bounded by construction
  (`ALERT_ASSETS = 4`, `RATIONALE_LIMIT = 260`), so no change was needed. Re-measure if either constant grows.

## References

- [Bot API: `sendMessage`](https://core.telegram.org/bots/api#sendmessage) — the `text` parameter and its
  1–4096 limit, counted after entity parsing
- [Bot API: `MessageEntity`](https://core.telegram.org/bots/api#messageentity) — "Offset in UTF-16 code units to
  the start of the entity"
- [Entity length](https://core.telegram.org/api/entities#entity-length) — how length is computed, with the
  BMP-counts-1 / other-planes-count-2 rule and a byte-wise algorithm
- [Bot API: formatting options](https://core.telegram.org/bots/api#formatting-options) — the permitted HTML tags
- [Bot FAQ: limits](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this) — per-chat,
  per-group and broadcast rate limits
- [Unicode: Khmer block `U+1780`–`U+17FF`](https://www.unicode.org/charts/PDF/U1780.pdf) — entirely BMP, so Khmer
  text costs one UTF-16 unit per character

Code: [`app/telegram.py`](../app/telegram.py) (`width`, `_fits`, `chunks`, `send`),
[`app/social.py`](../app/social.py) (`daily_post`, `SECTION_ORDER`, `DROP_ORDER`),
[`app/briefs.py`](../app/briefs.py) (`build_daily` builds `operatorPost`),
[`app/config.py`](../app/config.py) (`brief_style`).
