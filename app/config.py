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


DEFAULT_MODELS = {
    'anthropic': {'extract': 'claude-haiku-4-5', 'analyst': 'claude-opus-5'},
    'ollama': {'extract': 'llama3.1:8b', 'analyst': 'llama3.1:8b'},
    'mock': {'extract': 'mock', 'analyst': 'mock'},
}


def ai_provider() -> str:
    return env('AI_PROVIDER', 'anthropic').strip().lower() or 'anthropic'


def anthropic_configured() -> bool:
    return bool(env('ANTHROPIC_API_KEY') or env('ANTHROPIC_AUTH_TOKEN'))


def ollama_base_url() -> str:
    return (env('OLLAMA_BASE_URL') or 'http://host.docker.internal:11434').rstrip('/')


def ai_configured() -> bool:
    provider = ai_provider()
    if provider == 'anthropic':
        return anthropic_configured()
    return provider in ('ollama', 'mock')


def brief_narrative_enabled() -> bool:
    """The brief's data sections are deterministic; the narrative is model prose. An 8B local model was observed
    inventing an event that was not in its input ("the BOJ's decision to hike rates"), so the narrative is opt-in
    for ollama and on by default only where the model is strong enough to stay grounded."""
    explicit = env('BRIEF_USE_AI')
    if explicit:
        return explicit.strip().lower() in {'1', 'true', 'yes', 'on'}
    return ai_provider() != 'ollama'


def model_for(stage: str) -> str:
    """EXTRACT_MODEL / ANALYST_MODEL, falling back to the provider's default when unset or set for another provider."""
    provider = ai_provider()
    value = env('EXTRACT_MODEL' if stage == 'extract' else 'ANALYST_MODEL')
    if not value or (provider == 'ollama' and value.startswith('claude-')) or (provider == 'anthropic' and not value.startswith('claude-')):
        return DEFAULT_MODELS.get(provider, DEFAULT_MODELS['anthropic'])[stage]
    return value
