"""Environment-driven settings. Read at call time so tests and scripts can override per process."""
import os

ASSET_UNIVERSE = ['USD', 'EURUSD', 'XAUUSD', 'BTC', 'ETH', 'SPX', 'NASDAQ', 'US10Y', 'OIL']
SUPPORTED_LANGUAGES = {'en', 'km'}
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


def decompose_analysis() -> bool:
    """Split the analyst into a small economic read plus one narrow call per affected asset.

    A single call filling the whole nested Analysis schema is where an 8B model fell down: it hedged every score to
    zero, reused one rationale for eight assets and contradicted its own risk-regime call. Decomposed, each call has
    one job. Opus-class models handle the full schema in one pass, so they keep the single call (faster, cheaper).
    """
    explicit = env('ANALYST_DECOMPOSE')
    if explicit:
        return explicit.strip().lower() in {'1', 'true', 'yes', 'on'}
    return ai_provider() == 'ollama'


def brief_narrative_enabled() -> bool:
    """The brief's data sections are deterministic; the narrative is model prose. A local model was observed inventing
    an event that was not in its input ("the BOJ's decision to hike rates"), so narrative prose is checked for
    groundedness (app.grounding) before it is published, whichever provider wrote it."""
    explicit = env('BRIEF_USE_AI')
    if explicit:
        return explicit.strip().lower() in {'1', 'true', 'yes', 'on'}
    return True


def brief_style() -> str:
    """How the daily brief reaches the operator's chat: 'digest' (default) is the one-message read; 'full' is the
    complete brief, which Telegram splits into four messages. The full text is stored and served by
    GET /briefs/latest either way, so this only chooses what the phone receives.
    """
    value = env('TELEGRAM_BRIEF_STYLE', 'digest').strip().lower() or 'digest'
    return value if value in ('digest', 'full') else 'digest'


def output_language() -> str:
    """Language of the *delivered* text (Telegram messages, the rendered brief). Storage stays English: the database,
    the intelligence API and app.grounding all read English, and the gold/forex/crypto engines consume that API.
    An unsupported value falls back to English rather than shipping half-translated text."""
    value = env('OUTPUT_LANGUAGE', 'en').strip().lower() or 'en'
    return value if value in SUPPORTED_LANGUAGES else 'en'


def translate_model() -> str:
    """Translation is a delivery step, not analysis, so it has its own small model and ignores AI_PROVIDER: the local
    8B models are not good enough at Khmer to publish, and prose falls back to English without an Anthropic key."""
    return env('TRANSLATE_MODEL') or 'claude-haiku-4-5'


def model_for(stage: str) -> str:
    """EXTRACT_MODEL / ANALYST_MODEL, falling back to the provider's default when unset or set for another provider."""
    provider = ai_provider()
    value = env('EXTRACT_MODEL' if stage == 'extract' else 'ANALYST_MODEL')
    if not value or (provider == 'ollama' and value.startswith('claude-')) or (provider == 'anthropic' and not value.startswith('claude-')):
        return DEFAULT_MODELS.get(provider, DEFAULT_MODELS['anthropic'])[stage]
    return value
