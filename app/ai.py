"""Two-stage AI: a cheap extractor and a reasoning analyst, both through the official Anthropic SDK, plus an offline
mock used by tests and the smoke run."""
import copy
import json
import logging
import re
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel

import httpx

from app.config import ai_provider, anthropic_configured, env, env_int, model_for, ollama_base_url
from app.prompts import ANALYST_SYSTEM, BRIEF_SYSTEM, EXTRACTOR_SYSTEM
from app.schemas import Analysis, Extraction

log = logging.getLogger('eco.ai')
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
_STRIP_KEYS = {'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf', 'minLength', 'maxLength',
               'pattern', 'minItems', 'maxItems', 'format', 'default', 'title', 'examples'}
M = TypeVar('M', bound=BaseModel)


class AIError(Exception):
    pass


class AINotConfigured(AIError):
    pass


class AIRefused(AIError):
    pass


def strict_schema(model: type[BaseModel]) -> dict:
    """JSON schema in the structured-outputs subset: every object closed, every property required, no numeric or
    string constraints (Pydantic enforces those client-side after parsing)."""
    def walk(node):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {key: walk(value) for key, value in node.items() if key not in _STRIP_KEYS}
        if out.get('type') == 'object' and 'properties' in out:
            out['additionalProperties'] = False
            out['required'] = list(out['properties'].keys())
        return out
    return walk(copy.deepcopy(model.model_json_schema()))


def inline_refs(schema: dict) -> dict:
    """Resolve local $ref/$defs so grammar-based decoders (Ollama) see one flat schema; our schemas are not recursive."""
    defs = schema.get('$defs', {})

    def walk(node):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if '$ref' in node and node['$ref'].startswith('#/$defs/'):
            target = defs[node['$ref'].split('/')[-1]]
            merged = {**walk(target), **{k: v for k, v in node.items() if k != '$ref'}}
            return merged
        return {key: walk(value) for key, value in node.items() if key != '$defs'}
    return walk(schema)


def _call_ollama(model: type[M], model_id: str, system: str, user: str, num_ctx: int, num_predict: int) -> M:
    schema = inline_refs(strict_schema(model))
    payload = {'model': model_id, 'stream': False, 'format': schema, 'keep_alive': '30m',
               'options': {'temperature': 0, 'num_ctx': num_ctx, 'num_predict': num_predict},
               'messages': [{'role': 'system', 'content': system},
                            {'role': 'user', 'content': user + '\n\nRespond with one JSON object that matches the schema; '
                                                            'no prose before or after it.'}]}
    try:
        with httpx.Client(timeout=float(env_int('AI_TIMEOUT_SECONDS', 300))) as client:
            response = client.post(ollama_base_url() + '/api/chat', json=payload)
    except httpx.HTTPError as error:
        raise AIError(f'ollama unreachable at {ollama_base_url()}: {error}') from error
    if response.status_code == 404 and 'not found' in response.text:
        raise AINotConfigured(f'ollama model {model_id!r} is not pulled: {response.text[:160]}')
    if response.status_code >= 400:
        raise AIError(f'ollama {response.status_code}: {response.text[:200]}')
    body = response.json()
    if body.get('done_reason') == 'length':
        raise AIError('ollama response truncated at num_predict')
    log.info('%s prompt=%s out=%s %.1fs', model_id, body.get('prompt_eval_count'), body.get('eval_count'),
             (body.get('total_duration') or 0) / 1e9)
    content = (body.get('message') or {}).get('content') or ''
    try:
        return model.model_validate_json(content)
    except ValueError as error:
        raise AIError(f'ollama output failed schema validation: {str(error)[:200]}') from error


def _client():
    if not anthropic_configured():
        raise AINotConfigured('Set ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) or use AI_PROVIDER=mock')
    import anthropic
    return anthropic.Anthropic(timeout=float(env_int('AI_TIMEOUT_SECONDS', 180)), max_retries=2)


def _parse(response, model: type[M]) -> M:
    if response.stop_reason == 'refusal':
        details = getattr(response, 'stop_details', None)
        raise AIRefused(f'model declined: {getattr(details, "category", None)} {getattr(details, "explanation", "")}')
    if response.stop_reason == 'max_tokens':
        raise AIError('response truncated at max_tokens')
    text = next((block.text for block in response.content if block.type == 'text'), None)
    if text is None:
        raise AIError('no text block in response')
    return model.model_validate_json(text)


def _call_anthropic(model: type[M], model_id: str, system: str, user: str, max_tokens: int, reasoning: bool) -> M:
    import anthropic
    client = _client()
    schema = {'type': 'json_schema', 'schema': strict_schema(model)}
    try:
        if not reasoning:
            response = client.messages.create(model=model_id, max_tokens=max_tokens, system=system,
                                              messages=[{'role': 'user', 'content': user}],
                                              output_config={'format': schema})
        else:
            kwargs = {}
            if model_id.startswith(('claude-opus-5', 'claude-fable')):
                kwargs = {'betas': [FALLBACK_BETA], 'fallbacks': 'default'}
            response = client.beta.messages.create(
                model=model_id, max_tokens=max_tokens,
                thinking={'type': 'adaptive'},
                output_config={'effort': env('ANALYST_EFFORT', 'high') or 'high', 'format': schema},
                system=[{'type': 'text', 'text': system, 'cache_control': {'type': 'ephemeral'}}],
                messages=[{'role': 'user', 'content': user}], **kwargs)
    except anthropic.RateLimitError as error:
        raise AIError(f'rate limited: {error.message}') from error
    except anthropic.APIStatusError as error:
        raise AIError(f'api error {error.status_code}: {error.message}') from error
    except anthropic.APIConnectionError as error:
        raise AIError(f'connection error: {error}') from error
    usage = getattr(response, 'usage', None)
    if usage is not None:
        log.info('%s in=%s cached=%s out=%s', model_id, usage.input_tokens, getattr(usage, 'cache_read_input_tokens', 0),
                 usage.output_tokens)
    return _parse(response, model)


def extract(article: dict) -> Extraction:
    if ai_provider() == 'mock':
        return mock_extract(article)
    user = json.dumps({
        'source': article.get('source_name'), 'source_reliability': article.get('reliability'),
        'published_at': str(article.get('published_at')), 'headline': article.get('headline'),
        'content': (article.get('content') or '')[:6000], 'url': article.get('url'),
        'keyword_hints': {'categories': article.get('categories'), 'assets': article.get('assets'),
                          'countries': article.get('countries')},
    }, default=str)
    if ai_provider() == 'ollama':
        return _call_ollama(Extraction, model_for('extract'), EXTRACTOR_SYSTEM, user, num_ctx=8192, num_predict=1500)
    return _call_anthropic(Extraction, model_for('extract'), EXTRACTOR_SYSTEM, user, max_tokens=2048, reasoning=False)


def analyze(context: dict) -> Analysis:
    if ai_provider() == 'mock':
        return mock_analyze(context)
    if ai_provider() == 'ollama':
        return _call_ollama(Analysis, model_for('analyst'), ANALYST_SYSTEM, json.dumps(context, default=str),
                            num_ctx=16384, num_predict=4096)
    return _call_anthropic(Analysis, model_for('analyst'), ANALYST_SYSTEM, json.dumps(context, default=str),
                           max_tokens=16000, reasoning=True)


def narrate_brief(brief: dict) -> str | None:
    if ai_provider() == 'mock':
        return None
    if ai_provider() == 'ollama':
        payload = {'model': model_for('analyst'), 'stream': False, 'keep_alive': '30m',
                   'options': {'temperature': 0.3, 'num_ctx': 16384, 'num_predict': 500},
                   'messages': [{'role': 'system', 'content': BRIEF_SYSTEM},
                                {'role': 'user', 'content': json.dumps(brief, default=str)}]}
        try:
            with httpx.Client(timeout=float(env_int('AI_TIMEOUT_SECONDS', 300))) as client:
                response = client.post(ollama_base_url() + '/api/chat', json=payload)
                response.raise_for_status()
            return ((response.json().get('message') or {}).get('content') or '').strip() or None
        except (httpx.HTTPError, ValueError) as error:
            log.warning('brief narrative skipped: %s', error)
            return None
    import anthropic
    client = _client()
    model_id = model_for('analyst')
    try:
        response = client.messages.create(model=model_id, max_tokens=2048, system=BRIEF_SYSTEM,
                                          output_config={'effort': 'medium'},
                                          messages=[{'role': 'user', 'content': json.dumps(brief, default=str)}])
    except anthropic.APIError as error:
        log.warning('brief narrative skipped: %s', error)
        return None
    if response.stop_reason == 'refusal':
        return None
    return next((block.text.strip() for block in response.content if block.type == 'text'), None)


# ---- offline mock -----------------------------------------------------------------------------------------------
_NUM = r'(-?\d+(?:\.\d+)?)'
_TYPE_HINTS = [
    (r'\bcpi\b|consumer price', 'CPI_RELEASE', 'INFLATION'), (r'\bpce\b', 'PCE_RELEASE', 'INFLATION'),
    (r'\bppi\b|producer price', 'PPI_RELEASE', 'INFLATION'),
    (r'nonfarm|non-farm|payroll', 'NFP_RELEASE', 'EMPLOYMENT'), (r'jobless|initial claims', 'JOBLESS_CLAIMS', 'EMPLOYMENT'),
    (r'\bgdp\b', 'GDP_RELEASE', 'GROWTH'), (r'retail sales', 'RETAIL_SALES_RELEASE', 'GROWTH'),
    (r'fomc|rate decision|fed (holds|cuts|hikes|raises|lowers)', 'FOMC_DECISION', 'MONETARY_POLICY'),
    (r'\bspeech\b|remarks|testif', 'FED_SPEECH', 'MONETARY_POLICY'), (r'\becb\b', 'ECB_DECISION', 'MONETARY_POLICY'),
    (r'\bsec\b.*(charg|sue|approv|lawsuit)|(charg|sue|approv|lawsuit).*\bsec\b', 'SEC_ACTION', 'CRYPTO'),
    (r'\betf\b', 'CRYPTO_ETF', 'CRYPTO'), (r'hack|exploit|drain', 'CRYPTO_HACK', 'CRYPTO'),
    (r'stablecoin|usdt|usdc|tether', 'STABLECOIN', 'CRYPTO'), (r'tariff', 'TARIFFS_TRADE', 'GLOBAL_GEOPOLITICAL'),
    (r'\bwar\b|missile|invasion|ceasefire', 'WAR_CONFLICT', 'GLOBAL_GEOPOLITICAL'), (r'opec|crude|brent', 'OIL_OPEC', 'GLOBAL_GEOPOLITICAL'),
]


def _num(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def mock_extract(article: dict) -> Extraction:
    text = f"{article.get('headline', '')} {article.get('content', '')}"
    event_type, category = 'MARKET_COMMENTARY', None
    for pattern, kind, cat in _TYPE_HINTS:
        if re.search(pattern, text, re.IGNORECASE):
            event_type, category = kind, cat
            break
    categories = list(article.get('categories') or [])
    if category and category not in categories:
        categories.insert(0, category)
    published = article.get('published_at') or datetime.now(timezone.utc)
    actual = _num(r'actual\s*:?\s*' + _NUM, text) or _num(_NUM + r'%?\s*(?:vs|versus)', text)
    forecast = _num(r'(?:forecast|expected|consensus|estimate)s?\s*(?:of|:)?\s*' + _NUM, text) or \
        _num(r'(?:vs|versus)\.?\s*' + _NUM + r'%?\s*(?:forecast|expected|consensus|estimate)', text)
    previous = _num(r'(?:prev(?:ious)?|prior)\s*:?\s*' + _NUM, text)
    surprise = 'NOT_APPLICABLE'
    if actual is not None and forecast is not None:
        surprise = 'ABOVE_EXPECTATIONS' if actual > forecast else 'BELOW_EXPECTATIONS' if actual < forecast else 'IN_LINE'
    countries = list(article.get('countries') or ['US'])
    is_reaction = bool(re.search(r'\b(falls?|drops?|jumps?|rallies|slides?|surges?|tumbles?)\b.*\bafter\b', text, re.I))
    return Extraction(
        is_relevant=bool(categories), event_type=event_type, subject=(article.get('headline') or 'event')[:60],
        event_date=published.strftime('%Y-%m-%d'), countries=countries, categories=categories or ['GLOBAL_GEOPOLITICAL'],
        institutions=list(article.get('entities') or []), people=[], assets=list(article.get('assets') or []),
        metric=event_type.replace('_RELEASE', '').replace('_', ' ') if event_type.endswith('_RELEASE') else None,
        actual=actual, forecast=forecast, previous=previous, unit='%' if actual is not None else None,
        surprise=surprise, tone='NOT_APPLICABLE', is_market_reaction_coverage=is_reaction,
        importance=int(article.get('importance_prior') or 40), fact_summary=(article.get('headline') or '')[:300])


def mock_analyze(context: dict) -> Analysis:
    event = context.get('event', {})
    facts = event.get('facts', {})
    categories = event.get('categories') or []
    surprise = facts.get('surprise')
    sign = 1 if surprise == 'ABOVE_EXPECTATIONS' else -1 if surprise == 'BELOW_EXPECTATIONS' else 0
    impacts, updates, chain = [], [], []
    interpretation = {'inflation': 'NEUTRAL', 'growth': 'NEUTRAL', 'employment': 'NEUTRAL', 'liquidity': 'NEUTRAL'}
    fed = 'NEUTRAL'
    if 'INFLATION' in categories and sign:
        interpretation['inflation'] = 'HOTTER' if sign > 0 else 'COOLER'
        interpretation['liquidity'] = 'TIGHTER' if sign > 0 else 'LOOSER'
        fed = 'MORE_HAWKISH' if sign > 0 else 'MORE_DOVISH'
        for asset, score in (('USD', 60), ('US10Y', 60), ('XAUUSD', -50), ('BTC', -45), ('SPX', -30), ('EURUSD', -45)):
            value = int(score * sign)
            impacts.append({'asset': asset, 'immediate': {'direction': 'NEUTRAL', 'score': value},
                            'short_term': {'direction': 'NEUTRAL', 'score': int(value * 0.7)},
                            'medium_term': {'direction': 'NEUTRAL', 'score': int(value * 0.3)},
                            'rationale': 'Textbook inflation-surprise channel (mock analyst).'})
        updates = [{'region': 'US', 'dimension': 'inflation', 'direction': sign, 'magnitude': 60,
                    'reason': 'Inflation print vs consensus'},
                   {'region': 'US', 'dimension': 'monetary_policy', 'direction': sign, 'magnitude': 35,
                    'reason': 'Implied policy path'}]
        chain = ['Inflation print differs from consensus', 'Market re-prices the Fed path',
                 'Front-end yields and USD move', 'Financial conditions shift', 'Rate-sensitive and risk assets re-price']
    elif 'CRYPTO' in categories:
        impacts = [{'asset': 'BTC', 'immediate': {'direction': 'NEUTRAL', 'score': 20},
                    'short_term': {'direction': 'NEUTRAL', 'score': 15}, 'medium_term': {'direction': 'NEUTRAL', 'score': 5},
                    'rationale': 'Crypto-native development (mock analyst).'}]
        updates = [{'region': 'CRYPTO', 'dimension': 'market_structure', 'direction': 0, 'magnitude': 10, 'reason': 'Mock'}]
        chain = ['Crypto-native development', 'Sentiment and flows adjust', 'BTC/ETH re-price']
    return Analysis(
        summary=f"Mock analysis of {event.get('title', 'event')}.", what_happened=facts.get('fact_summary') or event.get('title', ''),
        why_it_matters='Deterministic stand-in used when AI_PROVIDER=mock.', what_changed_vs_expectations=str(surprise),
        is_new_information=True, economic_interpretation=interpretation,
        central_bank_implication={'fed': fed, 'ecb': 'NOT_RELEVANT',
                                  'rate_cut_probability_impact': 'LOWER' if sign > 0 else 'HIGHER' if sign < 0 else 'UNCHANGED'},
        risk_regime_impact='RISK_OFF' if sign > 0 and 'INFLATION' in categories else 'NEUTRAL',
        causal_chain=chain or ['Event observed', 'No strong macro channel identified'], relation_to_trend='NEW_THEME',
        horizon='SHORT_TERM', evidence_strength='MODERATE', confidence=70, asset_impacts=impacts,
        macro_state_updates=updates, key_risks=['Mock provider: no real reasoning performed'])
