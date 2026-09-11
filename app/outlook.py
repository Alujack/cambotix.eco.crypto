"""Per-asset outlook in plain words: which way the engine expects an asset to go, over which horizon, and why.

No judgement is made here. The numbers come from app.intel's decay-weighted aggregate of the analyst's per-event
scores, and the reason is the analyst's own per-asset rationale. This module is the layer that says all of it the way
an analyst would say it out loud, because a score of -38 on XAUUSD tells a reader nothing on its own: they need the
direction in words, the path across the three horizons, the mechanism, and how much evidence stands behind it.

The engine's own track record is part of that: a call is worth what its history says it is worth, so the hit rate
measured in `market_reactions` travels with the outlook rather than sitting in a separate quality endpoint.
"""
from app.config import ASSET_UNIVERSE
from app.intel import bias_label

# The instrument in the reader's words, and what a positive score actually means for it. US10Y is the one that trips
# people up (a score is the yield, not the bond), so it says so every time it is delivered.
ASSET_NAMES = {
    'USD': 'US dollar', 'EURUSD': 'Euro / dollar', 'XAUUSD': 'Gold', 'BTC': 'Bitcoin', 'ETH': 'Ethereum',
    'SPX': 'S&P 500', 'NASDAQ': 'Nasdaq 100', 'US10Y': 'US 10-year yield', 'OIL': 'Oil',
}
UP_MEANS = {
    'USD': 'dollar stronger', 'EURUSD': 'euro stronger against the dollar', 'XAUUSD': 'gold price higher',
    'BTC': 'BTC price higher', 'ETH': 'ETH price higher', 'SPX': 'index higher', 'NASDAQ': 'index higher',
    'US10Y': 'yield higher — bond prices lower', 'OIL': 'crude higher',
}
HORIZONS = ['immediate', 'short_term', 'medium_term']
# Below this the aggregate is noise, not a read; those assets are named in one line instead of getting a paragraph.
MATERIAL = 8


def clip(text, limit: int) -> str:
    """Delivered text is read on a phone; an untrimmed headline or rationale pushes everything else off the screen.

    The cut lands on a word boundary when one is within reach: "the next print approaches (moc…" reads as a defect
    in a published post, where "the next print approaches…" reads as a deliberate trim. A single long word with no
    boundary to find is still cut where the limit falls.
    """
    text = str(text)
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rstrip()
    boundary = cut.rfind(' ')
    if boundary > limit // 2 and boundary >= len(cut) - 24:
        cut = cut[:boundary]
    return cut.rstrip(' ,;:.-—') + '…'


def name(asset: str) -> str:
    return ASSET_NAMES.get(asset, asset)


def up_means(asset: str) -> str:
    return UP_MEANS.get(asset, 'price higher')


def path(horizons: dict) -> str:
    """How the pressure travels: a hot print that fades in a week is a different trade from one that compounds."""
    now, later = horizons['immediate']['score'], horizons['medium_term']['score']
    if now and later and (now > 0) != (later > 0) and abs(later) >= 10:
        return 'FLIPPING'
    if abs(later) >= abs(now) + 8:
        return 'BUILDING'
    if abs(now) >= abs(later) + 8:
        return 'FADING'
    return 'STEADY'


def impact_horizons(impact) -> dict:
    """One analysis's AssetImpact in the {horizon: {score}} shape the helpers here read."""
    return {'immediate': {'score': impact.immediate.score}, 'short_term': {'score': impact.short_term.score},
            'medium_term': {'score': impact.medium_term.score}}


def direction(horizons: dict) -> str:
    """The horizon carrying the most weight names the direction: an event that does nothing in the first hours and
    then moves an asset all week is "leaning lower", not "no clear direction"."""
    if not horizons:
        return 'NEUTRAL'
    return bias_label(max(horizons.values(), key=lambda h: abs(h['score']))['score'])


def evidence(mass: float) -> str:
    """app.intel weights each event by importance x confidence x recency; mass 0.6 is one solid event's worth."""
    if mass >= 1.2:
        return 'EVIDENCE_SOLID'
    if mass >= 0.5:
        return 'EVIDENCE_FAIR'
    return 'EVIDENCE_THIN'


def track_record(conn, days: int = 30) -> dict:
    """How often the engine's expected direction was confirmed by the actual price move, per asset.

    Only windows where a direction was actually expected count; FLAT outcomes are reported but kept out of the hit
    rate, and a rate is withheld below MIN_CHECKS because 2-of-3 reads as skill when it is noise.
    """
    MIN_CHECKS = 5
    rows = conn.execute('''SELECT asset, interpretation, count(*) AS n FROM market_reactions
                           WHERE measured_at IS NOT NULL AND interpretation IS NOT NULL
                             AND expected_direction IN ('BULLISH', 'BEARISH')
                             AND window_label IN ('1h', '4h', '24h')
                             AND measured_at >= now() - make_interval(days => %s)
                           GROUP BY asset, interpretation''', (days,)).fetchall()
    tally: dict[str, dict] = {}
    for row in rows:
        bucket = tally.setdefault(row['asset'], {'CONFIRMED': 0, 'REJECTED': 0, 'FLAT': 0})
        bucket[row['interpretation']] = row['n']
    record = {}
    for asset, bucket in tally.items():
        directional = bucket['CONFIRMED'] + bucket['REJECTED']
        record[asset] = {'confirmed': bucket['CONFIRMED'], 'rejected': bucket['REJECTED'], 'flat': bucket['FLAT'],
                         'checks': directional, 'days': days,
                         'hitRate': round(100 * bucket['CONFIRMED'] / directional) if directional >= MIN_CHECKS else None}
    return record


def rows(assets: dict, record: dict | None = None, limit: int = 6) -> dict:
    """One outlook row per asset with a real read, plus the names of the ones sitting flat.

    `assets` is app.intel.macro_current()['assets'] - already computed for the brief, so this costs no queries.
    """
    record = record or {}
    material, quiet = [], []
    for asset in ASSET_UNIVERSE:
        bias = assets.get(asset) or {}
        horizons = bias.get('horizons') or {}
        if not horizons or abs(bias.get('score', 0)) < MATERIAL:
            quiet.append(asset)
            continue
        drivers = [d for d in (bias.get('drivers') or []) if (d.get('rationale') or '').strip()][:2]
        material.append({
            'asset': asset, 'name': name(asset), 'upMeans': up_means(asset),
            # The horizon carrying the move names the direction, not the blended score: an asset can clear MATERIAL
            # on a strong immediate read and still blend back inside bias_label's +/-15 NEUTRAL band, which printed
            # "no clear direction" above three same-signed numbers. `macroBias` stays on the API for the engines.
            'score': bias['score'], 'direction': direction(horizons),
            'horizons': {h: {'score': horizons[h]['score'], 'direction': horizons[h]['direction']}
                         for h in HORIZONS if h in horizons},
            'path': path(horizons), 'evidence': evidence(horizons.get('short_term', {}).get('evidence', 0.0)),
            'eventCount': len(bias.get('drivers') or []),
            'drivers': [{'eventId': d.get('eventId'), 'title': d.get('title'), 'importance': d.get('importance'),
                         'rationale': (d.get('rationale') or '').strip()} for d in drivers],
            'trackRecord': record.get(asset),
        })
    material.sort(key=lambda row: -abs(row['score']))
    return {'material': material[:limit], 'quiet': quiet + [row['asset'] for row in material[limit:]]}


def prose(outlook: dict) -> list[str]:
    """The sentences in an outlook that a model wrote, for the translator. Tickers and numbers are not in here."""
    return [driver['rationale'] for row in outlook.get('material') or [] for driver in row['drivers']
            if driver.get('rationale')]


def translated(outlook: dict, delivered: dict) -> dict:
    """The same outlook with each rationale swapped for its delivery-language text (falling back to English)."""
    return {**outlook,
            'material': [{**row, 'drivers': [{**d, 'rationale': delivered.get(d['rationale'], d['rationale'])}
                                             for d in row['drivers']]}
                         for row in outlook.get('material') or []]}
