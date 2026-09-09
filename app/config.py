"""Environment-driven settings. Read at call time so tests and scripts can override per process."""
import os

ASSET_UNIVERSE = ['USD', 'EURUSD', 'XAUUSD', 'BTC', 'ETH', 'SPX', 'NASDAQ', 'US10Y', 'OIL']
CATEGORIES = ['INFLATION', 'EMPLOYMENT', 'GROWTH', 'MONETARY_POLICY', 'GLOBAL_GEOPOLITICAL', 'CRYPTO']
MEDIA_SOURCE_CATEGORIES = {'financial_media', 'crypto_media'}


def env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, '') or default)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == '':
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def ai_provider() -> str:
    return env('AI_PROVIDER', 'anthropic').strip().lower() or 'anthropic'


def anthropic_configured() -> bool:
    return bool(env('ANTHROPIC_API_KEY') or env('ANTHROPIC_AUTH_TOKEN'))
