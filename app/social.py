"""Publishable posts: the engine's own intelligence rendered as content for a public channel or page.

A brief and an event alert are written for one operator: they carry pipeline counts, event ids, model names, the
whole asset universe and a fixed-width layout that only survives inside a Telegram <pre> block. A post is read by
strangers scrolling on a phone, so the same data is rendered as news here - one hook, the state of play, what it
means market by market, what is coming, and what would change the read - with the engine's evidence and track
record attached in place of its plumbing.

Three rules hold every post together:
  - No trading advice. No entries, stops, sizing or price targets: economic impact only, and every post says so.
  - Nothing is invented for the sake of punch. Every line is computed from the data, or it is prose that already
    passed a gate elsewhere - the brief's narrative (app.grounding) or the analyst's own summary and rationales.
    This module never asks a model for a headline.
  - Platform differences are rendering only. Telegram takes HTML, Facebook and everywhere else take plain text
    (a <b> tag would be published verbatim), and the words are identical in both.

Delivery language follows app.i18n like every other delivered text. Hashtags are the exception: they are discovery
tokens rather than prose, so they stay Latin-script English in every language.
"""
import html
import re
from datetime import datetime, timezone

from app import i18n, outlook
from app.config import output_language
from app.macro_state import light

PLATFORMS = {
    # 3800 keeps a post under app.telegram.CHUNK, so a channel post is never split across two messages mid-tag.
    'telegram': {'parseMode': 'HTML', 'escape': True, 'bold': ('<b>', '</b>'), 'limit': 3800},
    'facebook': {'parseMode': None, 'escape': False, 'bold': ('', ''), 'limit': 3000},
}
POST_ASSETS = 3        # a post argues the markets this data moves hardest, not the whole universe
# An asset the event does not move is not news: below this the analyst's own scoring calls it NEUTRAL anyway
# (app.schemas.AssetImpact). The operator's alert still lists it - "the analyst scored USD flat" is worth knowing
# when you are watching the engine; it is filler when you are reading a channel.
POST_MIN_SCORE = 10
POST_DIMENSIONS = 3
POST_RELEASES = 3
POST_RISKS = 2
LEDE_LIMIT = 320       # Facebook folds a post behind "See more"; the hook has to land above the fold
RATIONALE_LIMIT = 180
CHAIN_STEPS = 4
TITLE_LIMIT = 160
MAX_TAGS = 8
# Sections in reading order, then the order they are given up in when a post would exceed the platform's limit.
# The markets read, the credit line and the tags are never dropped: they are the post.
SECTION_ORDER = ['head', 'lede', 'read', 'state', 'markets', 'chain', 'calendar', 'risks', 'credit', 'tags']
DROP_ORDER = ['calendar', 'risks', 'state', 'chain']

DIRECTION_MARK = {'BULLISH': '🔺', 'SLIGHT_BULLISH': '🔼', 'NEUTRAL': '➖', 'SLIGHT_BEARISH': '🔽', 'BEARISH': '🔻'}
FLAG = {'USD': '🇺🇸', 'EUR': '🇪🇺', 'GBP': '🇬🇧', 'JPY': '🇯🇵', 'CNY': '🇨🇳', 'CAD': '🇨🇦', 'AUD': '🇦🇺',
        'NZD': '🇳🇿', 'CHF': '🇨🇭'}
# Discovery tags, English and Latin-script in every delivery language: a Khmer reader still searches #Gold.
ASSET_TAGS = {'USD': '#USD', 'EURUSD': '#EURUSD', 'XAUUSD': '#Gold', 'BTC': '#Bitcoin', 'ETH': '#Ethereum',
              'SPX': '#SP500', 'NASDAQ': '#Nasdaq', 'US10Y': '#Bonds', 'OIL': '#Oil'}
CATEGORY_TAGS = {'INFLATION': '#Inflation', 'EMPLOYMENT': '#Jobs', 'GROWTH': '#Growth',
                 'MONETARY_POLICY': '#CentralBanks', 'GLOBAL_GEOPOLITICAL': '#Geopolitics', 'CRYPTO': '#Crypto'}
DIMENSION_TAGS = {'inflation': '#Inflation', 'employment': '#Jobs', 'growth': '#Growth',
                  'monetary_policy': '#CentralBanks', 'liquidity': '#Liquidity', 'fiscal': '#Fiscal',
                  'risk_appetite': '#RiskSentiment', 'geopolitical_risk': '#Geopolitics', 'energy': '#Energy',
                  'regulation': '#Regulation', 'adoption': '#Crypto', 'market_structure': '#Crypto'}
BANK_TAGS = {'fed': '#Fed', 'ecb': '#ECB'}
BASE_TAGS = ['#Macro', '#Markets', '#Cambotix']


def daily_post(brief: dict, platform: str = 'telegram', lang: str | None = None) -> dict:
    """The daily brief as a post. `brief` is app.briefs.build_daily's dict - already localized when it is delivered
    in another language, so no translation happens here."""
    lang = lang or output_language()
    style, L = _style(platform), i18n.labels(lang)
    esc, head = _writers(style)
    outlook_view = brief.get('assetOutlook') or {}
    material = (outlook_view.get('material') or [])[:POST_ASSETS]
    dimensions = _dimension_rows(brief)
    sections = {'head': [head(_tight(f"{L['social_daily_title']} · {brief.get('date', '')} · "
                                     f"{_stamp(brief.get('generatedAt'))}"))]}
    lede = _lede(brief)
    if lede:
        sections['lede'] = [esc(lede)]
    state = [f"{light(dim, value['score'])} {esc(_tight(f'{region} {i18n.dimension(dim, lang)}'))} — "
             f"{esc(i18n.state_reading(dim, value['state'], value['trend'], lang))} "
             f"{i18n.TREND_ARROW.get(value['trend'], '')} ({value['score']:+d})"
             for region, dim, value in dimensions]
    if brief.get('riskRegime'):
        state.append(esc(_tight(L['risk_liquidity'].format(
            risk=i18n.state_label('risk_appetite', brief['riskRegime'], lang),
            liquidity=i18n.enum(brief.get('globalLiquidity') or 'UNKNOWN', lang)))))
    if state:
        sections['state'] = [head(L['social_state'])] + state
    if material:
        sections['markets'] = [head(L['social_markets'])]
        for row in material:
            sections['markets'] += _market_lines(row, lang, L, esc)
    releases = [r for r in brief.get('upcoming') or [] if r.get('impact') == 'HIGH'][:POST_RELEASES]
    if releases:
        sections['calendar'] = [head(L['social_calendar'])] + [_release_line(r, lang, L, esc) for r in releases]
    risks = [risk for risk in brief.get('keyRisks') or [] if str(risk).strip()][:POST_RISKS]
    if risks:
        sections['risks'] = [head(L['social_risks'])] + [f'• {esc(outlook.clip(risk, 200))}' for risk in risks]
    sections['credit'] = _credit(brief, material, lang, L, esc)
    sections['tags'] = [' '.join(hashtags(assets=[row['asset'] for row in material],
                                          dimensions=[dim for _, dim, _ in dimensions]))]
    return _post(platform, style, sections, sections['tags'][0].split())


def event_post(event: dict, analysis, platform: str = 'telegram', lang: str | None = None,
               article_count: int | None = None) -> dict:
    """One analyzed event as a breaking-news post. Same analysis the private alert renders, without the plumbing:
    no event id and no model name, because a public reader is served by the evidence, not the internals."""
    lang = lang or output_language()
    style, L = _style(platform), i18n.labels(lang)
    esc, head = _writers(style)
    importance = int(event.get('importance') or 0)
    badge = L['social_breaking'] if importance >= 90 else L['social_alert'] if importance >= 80 else L['social_update']
    moved = [impact for impact in analysis.asset_impacts
             if max(abs(impact.immediate.score), abs(impact.short_term.score),
                    abs(impact.medium_term.score)) > POST_MIN_SCORE]
    ranked = sorted(moved, key=lambda impact: -abs(impact.immediate.score))[:POST_ASSETS]
    steps = list(analysis.causal_chain[:CHAIN_STEPS])
    # One translation call for the whole post; each segment falls back to its English text on failure.
    prose = i18n.translate([analysis.summary] + steps + [impact.rationale for impact in ranked], lang)
    summary, chain, rationales = prose[0], prose[1:1 + len(steps)], prose[1 + len(steps):]
    sections = {'head': [head(f"{badge} · {outlook.clip(event.get('title', ''), TITLE_LIMIT)}")],
                'lede': [esc(outlook.clip(_sentences(summary, 3), LEDE_LIMIT))],
                'read': [head(L['social_read']), esc(' · '.join(economic_read(analysis, lang, L)))]}
    if ranked:
        sections['markets'] = [head(L['social_markets'])]
        for impact, rationale in zip(ranked, rationales):
            horizons = outlook.impact_horizons(impact)
            row = {'asset': impact.asset, 'direction': outlook.direction(horizons), 'path': outlook.path(horizons),
                   'horizons': horizons, 'drivers': [{'rationale': rationale}]}
            sections['markets'] += _market_lines(row, lang, L, esc)
    if chain:
        sections['chain'] = [head(L['social_chain']), esc(' → '.join(step.strip() for step in chain))]
    tail = [L['social_stamp'].format(stamp=f'{datetime.now(timezone.utc):%H:%M}'),
            f"{L['confidence']} {analysis.confidence}",
            L['evidence'].format(evidence=i18n.enum(analysis.evidence_strength, lang).lower()),
            L['trend_relation'].format(relation=i18n.enum(analysis.relation_to_trend, lang))]
    if article_count:
        tail.append(L['source_items'].format(n=article_count))
    if i18n.translation_active(lang):
        tail.append(L['machine_translated'])
    sections['credit'] = [esc(_tight(' · '.join(tail))), esc(L['social_disclaimer'])]
    banks = [name for name, value in (('fed', analysis.central_bank_implication.fed),
                                      ('ecb', analysis.central_bank_implication.ecb)) if value != 'NOT_RELEVANT']
    sections['tags'] = [' '.join(hashtags(assets=[impact.asset for impact in ranked],
                                          categories=event.get('categories') or [], banks=banks))]
    return _post(platform, style, sections, sections['tags'][0].split())


def economic_read(analysis, lang: str, labels: dict | None = None) -> list[str]:
    """The event's economic read as words - inflation, growth, liquidity, the banks it moves, the risk regime.

    Shared with the private Telegram alert so the two renderings of one analysis can never drift apart. It lives
    here rather than in app.outlook because it needs app.i18n, and app.i18n imports app.outlook.
    """
    L = labels or i18n.labels(lang)
    interpretation, bank = analysis.economic_interpretation, analysis.central_bank_implication
    reads = [f"{L['read_inflation']} {i18n.enum(interpretation.inflation, lang)}",
             f"{L['read_growth']} {i18n.enum(interpretation.growth, lang)}",
             f"{L['read_liquidity']} {i18n.enum(interpretation.liquidity, lang)}"]
    if bank.fed != 'NOT_RELEVANT':
        reads.append(f'Fed {i18n.enum(bank.fed, lang)}')
    if bank.ecb != 'NOT_RELEVANT':
        reads.append(f'ECB {i18n.enum(bank.ecb, lang)}')
    reads.append(f"{L['read_risk']} {i18n.enum(analysis.risk_regime_impact, lang)}")
    return reads


def hashtags(assets=(), categories=(), dimensions=(), banks=(), extra=()) -> list[str]:
    """Subject tags first (what this post is about), then the standing ones - which keep their places rather than
    being trimmed off the end, because they are how a reader finds the rest of the feed."""
    subject = ([CATEGORY_TAGS[c] for c in categories if c in CATEGORY_TAGS]
               + [ASSET_TAGS[a] for a in assets if a in ASSET_TAGS]
               + [DIMENSION_TAGS[d] for d in dimensions if d in DIMENSION_TAGS]
               + [BANK_TAGS[b] for b in banks if b in BANK_TAGS] + list(extra))
    standing = [tag for tag in BASE_TAGS if tag not in subject]
    return list(dict.fromkeys(subject))[:max(MAX_TAGS - len(standing), 1)] + standing


# ---- rendering ---------------------------------------------------------------------------------------------------
def _style(platform: str) -> dict:
    style = PLATFORMS.get(platform)
    if style is None:
        raise ValueError(f'unknown platform {platform!r}; one of {", ".join(PLATFORMS)}')
    return style


def _writers(style: dict):
    """Escaping in one place: `esc` for anything data-derived, `head` for a section heading."""
    # quote=False: nothing here lands in an HTML attribute, and an apostrophe is worth more to a reader than &#x27;.
    esc = (lambda text: html.escape(str(text), quote=False)) if style['escape'] else (lambda text: str(text))
    open_bold, close_bold = style['bold']

    def head(text: str) -> str:
        return f'{open_bold}{esc(text)}{close_bold}'
    return esc, head


def _post(platform: str, style: dict, sections: dict, tags: list[str]) -> dict:
    text = _assemble(sections, style['limit'])
    return {'platform': platform, 'parseMode': style['parseMode'], 'text': text, 'chars': len(text), 'hashtags': tags}


def _assemble(sections: dict, limit: int) -> str:
    """Sections in reading order, giving up the least important ones until the post fits the platform."""
    keys = [key for key in SECTION_ORDER if sections.get(key)]
    droppable = [key for key in DROP_ORDER if key in keys]
    text = _join(sections, keys)
    while len(text) > limit and droppable:
        keys.remove(droppable.pop(0))
        text = _join(sections, keys)
    return text


def _join(sections: dict, keys: list[str]) -> str:
    return '\n\n'.join('\n'.join(sections[key]) for key in keys)


def _market_lines(row: dict, lang: str, L: dict, esc) -> list[str]:
    """One market: which way, how the pressure travels across the three horizons, and the mechanism."""
    horizons = row.get('horizons') or {}
    mark = DIRECTION_MARK.get(row['direction'], '➖')
    lines = [f"{mark} " + esc(_tight(L['outlook_headline'].format(
        name=i18n.asset_name(row['asset'], lang), asset=row['asset'],
        direction=i18n.outlook_word(row['direction'], lang), path=i18n.outlook_word(row['path'], lang))))]
    lines.append('   ' + esc(_tight(L['social_horizons'].format(
        now=_score(horizons.get('immediate')), week=_score(horizons.get('short_term')),
        months=_score(horizons.get('medium_term')), meaning=i18n.up_means(row['asset'], lang)))))
    driver = next((d for d in row.get('drivers') or [] if (d.get('rationale') or '').strip()), None)
    if driver:
        lines.append(f"   {esc(L['outlook_why'])}: " + esc(outlook.clip(driver['rationale'].strip(), RATIONALE_LIMIT)))
    return lines


def _release_line(release: dict, lang: str, L: dict, esc) -> str:
    currency = str(release.get('currency') or '')
    return (f"{FLAG.get(currency, '•')} {esc(_when(release.get('scheduledAt'), lang))} — {esc(currency)} "
            f"{esc(outlook.clip(release.get('title') or '', 52))} · {esc(L['forecast'])} "
            f"{esc(release.get('forecast') or '—')} · {esc(L['previous'])} {esc(release.get('previous') or '—')}")


def _credit(brief: dict, material: list, lang: str, L: dict, esc) -> list[str]:
    """Why a stranger should believe the post: how much was read, and how the engine's past calls actually did."""
    pipeline = brief.get('pipeline') or {}
    lines = []
    if pipeline.get('received24h'):
        lines.append(esc(_tight(L['social_evidence'].format(items=pipeline['received24h'],
                                                            sources=pipeline.get('sources24h') or 0))))
    record = _track_record(material)
    if record:
        lines.append(esc(_tight(L['social_track'].format(asset=record['asset'], hit=record['confirmed'],
                                                         total=record['checks'], days=record['days']))))
    if i18n.translation_active(lang):
        lines.append(esc(L['machine_translated']))
    lines.append(esc(L['social_disclaimer']))
    return lines


def _track_record(material: list) -> dict | None:
    """The measured record of the strongest market in the post, or nothing - a rate is withheld below the minimum
    number of checks in app.outlook, and a post must never imply a record the reactions table cannot support."""
    records = [{**(row.get('trackRecord') or {}), 'asset': row['asset']} for row in material
               if (row.get('trackRecord') or {}).get('hitRate') is not None]
    return max(records, key=lambda record: record['checks']) if records else None


def _dimension_rows(brief: dict, limit: int = POST_DIMENSIONS) -> list[tuple[str, str, dict]]:
    """The dimensions carrying the most pressure, whichever region they sit in; unknown ones are not news."""
    rows = [(region, dim, value) for region, dims in (brief.get('macroRegime') or {}).items()
            for dim, value in dims.items() if value and value.get('known')]
    rows.sort(key=lambda row: -abs(row[2].get('score') or 0))
    return rows[:limit]


def _lede(brief: dict) -> str | None:
    """The hook: prose that already passed a gate - the brief's grounded narrative, else the strongest analyzed
    event's own summary. Never a sentence this module wrote about the data."""
    text = str(brief.get('narrative') or '').strip()
    if not text:
        summaries = [str(d.get('summary') or '').strip() for d in brief.get('developments') or []]
        text = next((summary for summary in summaries if summary), '')
    return outlook.clip(_sentences(text, 2), LEDE_LIMIT) if text else None


def _sentences(text: str, count: int) -> str:
    """The first `count` sentences. A prefix of gated prose carries a subset of its claims, so it stays gated."""
    parts = re.split(r'(?<=[.!?។])\s+', str(text).strip())
    return ' '.join(parts[:count]).strip()


def _tight(text: str) -> str:
    """The brief's labels carry column padding for its fixed-width layout; a post is proportional text."""
    return re.sub(r'[ \t]{2,}', ' ', str(text)).strip()


def _score(horizon: dict | None) -> str:
    return f"{horizon['score']:+d}" if horizon else '—'


def _stamp(value) -> str:
    return f'{value:%H:%M} UTC' if isinstance(value, datetime) else str(value or '')[:16]


def _when(value, lang: str = 'en') -> str:
    if isinstance(value, datetime):
        return f'{i18n.weekday(f"{value:%a}", lang)} {value:%H:%M} UTC'
    return str(value or '')[:16]
