"""Market reaction tracker: anchor prices when an analysis lands, then measure what the market actually did."""
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import env, env_bool

log = logging.getLogger('eco.reactions')
WINDOWS = [('5m', 5), ('15m', 15), ('1h', 60), ('4h', 240), ('24h', 1440)]
COINGECKO_IDS = {'BTC': 'bitcoin', 'ETH': 'ethereum'}
ALPHA_VANTAGE_PAIRS = {'XAUUSD': ('XAU', 'USD'), 'EURUSD': ('EUR', 'USD')}
# Moves inside the threshold are "FLAT" rather than confirming or rejecting a call.
FLAT_THRESHOLD_PCT = {'BTC': 0.4, 'ETH': 0.5, 'XAUUSD': 0.15, 'EURUSD': 0.1}


def measurable(assets: list[str]) -> list[str]:
    allowed = set(COINGECKO_IDS)
    if env_bool('REACTIONS_FX_ENABLED', False) and env('ALPHA_VANTAGE_API_KEY'):
        allowed |= set(ALPHA_VANTAGE_PAIRS)
    return [asset for asset in assets if asset in allowed]


def fetch_prices(assets: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    crypto = [asset for asset in assets if asset in COINGECKO_IDS]
    try:
        if crypto:
            headers = {'x-cg-demo-api-key': env('COINGECKO_API_KEY')} if env('COINGECKO_API_KEY') else {}
            with httpx.Client(timeout=15, headers=headers) as client:
                response = client.get('https://api.coingecko.com/api/v3/simple/price',
                                      params={'ids': ','.join(COINGECKO_IDS[a] for a in crypto), 'vs_currencies': 'usd'})
                response.raise_for_status()
                data = response.json()
                for asset in crypto:
                    value = data.get(COINGECKO_IDS[asset], {}).get('usd')
                    if value is not None:
                        prices[asset] = float(value)
        for asset in assets:
            if asset in ALPHA_VANTAGE_PAIRS and env_bool('REACTIONS_FX_ENABLED', False) and env('ALPHA_VANTAGE_API_KEY'):
                base, quote = ALPHA_VANTAGE_PAIRS[asset]
                with httpx.Client(timeout=15) as client:
                    response = client.get('https://www.alphavantage.co/query', params={
                        'function': 'CURRENCY_EXCHANGE_RATE', 'from_currency': base, 'to_currency': quote,
                        'apikey': env('ALPHA_VANTAGE_API_KEY')})
                    response.raise_for_status()
                    rate = response.json().get('Realtime Currency Exchange Rate', {}).get('5. Exchange Rate')
                    if rate:
                        prices[asset] = float(rate)
    except (httpx.HTTPError, ValueError, KeyError) as error:
        log.warning('price fetch failed: %s', error)
    return prices


def open_windows(conn, event_id: str, impacts: list, anchor_at: datetime, prices: dict[str, float]) -> int:
    opened = 0
    for impact in impacts:
        price = prices.get(impact.asset)
        if price is None:
            continue
        for label, minutes in WINDOWS:
            conn.execute('''INSERT INTO market_reactions (event_id, asset, anchor_at, anchor_price, window_label, due_at,
                                                          expected_direction)
                            VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (event_id, asset, window_label) DO NOTHING''',
                         (event_id, impact.asset, anchor_at, price, label, anchor_at + timedelta(minutes=minutes),
                          impact.immediate.direction))
            opened += 1
    return opened


def interpret(expected: str | None, change_pct: float, asset: str) -> str:
    threshold = FLAT_THRESHOLD_PCT.get(asset, 0.3)
    if abs(change_pct) <= threshold:
        return 'FLAT'
    if expected == 'BULLISH':
        return 'CONFIRMED' if change_pct > 0 else 'REJECTED'
    if expected == 'BEARISH':
        return 'CONFIRMED' if change_pct < 0 else 'REJECTED'
    return 'REJECTED'


def measure_due(conn, prices: dict[str, float] | None = None, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    due = conn.execute('''SELECT id, asset, anchor_price, expected_direction FROM market_reactions
                          WHERE measured_at IS NULL AND due_at <= %s ORDER BY due_at LIMIT 100''', (now,)).fetchall()
    if not due:
        return 0
    if prices is None:
        prices = fetch_prices(sorted({row['asset'] for row in due}))
    measured = 0
    for row in due:
        price = prices.get(row['asset'])
        if price is None:
            continue
        change = (price / float(row['anchor_price']) - 1) * 100
        conn.execute('''UPDATE market_reactions SET price = %s, change_pct = %s, measured_at = %s, interpretation = %s
                        WHERE id = %s''', (price, round(change, 4), now, interpret(row['expected_direction'], change, row['asset']),
                                           row['id']))
        measured += 1
    return measured
