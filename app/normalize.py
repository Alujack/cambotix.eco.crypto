"""Deterministic normalization: hashing, timestamps, text cleanup and the keyword pre-classifier."""
import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from app.config import MEDIA_SOURCE_CATEGORIES

CATEGORY_PATTERNS = {
    'INFLATION': r'\b(cpi|pce|ppi|inflation\w*|price index|core prices|disinflation|deflation|import prices|'
                 r'wage growth|average hourly earnings|price pressures?)\b',
    'EMPLOYMENT': r'\b(nonfarm|non-farm|payrolls?|nfp|unemployment|jobless|jobs? (report|data|growth)|jolts|'
                  r'job openings|adp|layoffs?|hiring|labou?r market|participation rate|employment situation)\b',
    'GROWTH': r'\b(gdp|pmi|ism|retail sales|industrial production|durable goods|consumer (confidence|sentiment)|'
              r'housing starts|home sales|building permits|recession\w*|economic (growth|outlook|activity)|'
              r'manufacturing|services (index|sector))\b',
    'MONETARY_POLICY': r'\b(fed|fomc|federal reserve|powell|rate (cut|hike|decision|path)s?|interest rates?|'
                       r'basis points|bps|dot plot|quantitative (easing|tightening)|balance sheet|ecb|lagarde|'
                       r'bank of england|boe|bank of japan|boj|pboc|central banks?|hawkish|dovish|monetary policy|'
                       r'minutes|policy rate|federal funds)\b',
    'GLOBAL_GEOPOLITICAL': r'\b(war|ceasefire|missiles?|invasion|sanctions?|tariffs?|trade (war|deal|dispute|talks)|'
                           r'elections?|opec\+?|oil (prices?|output|supply)|crude|brent|wti|supply chains?|'
                           r'shutdown|debt ceiling|treasury (auction|yields?)|fiscal|deficit|stimulus|'
                           r'bank (failure|run|collapse)|credit (crunch|event)|geopolitic\w*)\b',
    'CRYPTO': r'\b(bitcoin|btc|ethereum|ether|eth|crypto\w*|blockchain|stablecoins?|usdt|tether|usdc|circle|'
              r'sec|cftc|etfs?|binance|coinbase|kraken|exchange|defi|tokens?|mining|miners?|halving|on-?chain|'
              r'solana|xrp|ripple|altcoins?|web3|digital assets?)\b',
}
ASSET_PATTERNS = {
    'USD': r'\b(dollar|usd|dxy|greenback)\b',
    'EURUSD': r'\b(euro|eur/?usd|eurusd)\b',
    'XAUUSD': r'\b(gold|xau|bullion)\b',
    'BTC': r'\b(bitcoin|btc)\b',
    'ETH': r'\b(ethereum|ether|eth)\b',
    'SPX': r'\b(s&p( 500)?|spx|stocks?|equities|wall street)\b',
    'NASDAQ': r'\b(nasdaq|tech stocks|ndx)\b',
    'US10Y': r'\b(treasur(y|ies)|yields?|10-year|bonds?)\b',
    'OIL': r'\b(oil|crude|brent|wti|opec\+?)\b',
}
COUNTRY_PATTERNS = {
    'US': r'\b(u\.s\.|us|united states|america\w*|fed|federal reserve|fomc|washington|bls|bea|treasury|sec|cftc)\b',
    'EU': r'\b(euro ?zone|euro area|ecb|europe\w*|eu|lagarde|eurostat)\b',
    'GB': r'\b(uk|u\.k\.|britain|british|bank of england|boe|sterling|pound)\b',
    'JP': r'\b(japan\w*|boj|bank of japan|yen)\b',
    'CN': r'\b(china|chinese|pboc|beijing|yuan|renminbi)\b',
}
INSTITUTION_PATTERNS = {
    'Federal Reserve': r'\b(fed|fomc|federal reserve)\b', 'ECB': r'\b(ecb|european central bank)\b',
    'BLS': r'\b(bls|bureau of labor statistics)\b', 'BEA': r'\b(bea|bureau of economic analysis)\b',
    'SEC': r'\bsec\b', 'CFTC': r'\bcftc\b', 'OPEC': r'\bopec\+?\b', 'US Treasury': r'\btreasury\b',
    'Bank of England': r'\b(boe|bank of england)\b', 'Bank of Japan': r'\b(boj|bank of japan)\b',
}
HIGH_SIGNAL = re.compile(r'\b(cpi|fomc|rate decision|nonfarm|payrolls|pce|gdp|fed (cuts?|hikes?|holds?|raises?|lowers?)|'
                         r'emergency|default|collapse\w*|bankrupt\w*|hack(ed|ers)?|exploit|approv(es|ed|al)|bans?|'
                         r'banned|shutdown|invasion|ceasefire|tariffs?|sanctions?|halts?|suspends?|lawsuit|charges?)\b',
                         re.IGNORECASE)
_COMPILED = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in CATEGORY_PATTERNS.items()}
_ASSETS = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in ASSET_PATTERNS.items()}
_COUNTRIES = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in COUNTRY_PATTERNS.items()}
_INSTITUTIONS = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in INSTITUTION_PATTERNS.items()}
_TAGS = re.compile(r'<[^>]+>')
_SPACES = re.compile(r'\s+')


def clean_text(value: str | None, limit: int = 4000) -> str:
    if not value:
        return ''
    text = _SPACES.sub(' ', _TAGS.sub(' ', html.unescape(str(value)))).strip()
    return text[:limit]


def parse_datetime(value) -> datetime | None:
    """ISO-8601, Alpha Vantage compact (20260909T143000), RFC-822 (RSS). Always tz-aware UTC."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        parsed = None
        if re.fullmatch(r'\d{8}T\d{6}', text):
            parsed = datetime.strptime(text, '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc)
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            except ValueError:
                parsed = None
        if parsed is None:
            try:
                parsed = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def content_hash(source_key: str, external_id: str | None, url: str | None, headline: str) -> str:
    identity = (external_id or url or _SPACES.sub(' ', headline.lower())).strip()
    return hashlib.sha256(f'{source_key}|{identity}'.encode()).hexdigest()


def article_id(digest: str) -> str:
    return 'art_' + digest[:20]


def classify(headline: str, content: str, source: dict, topics: list[str] | None = None) -> dict:
    """Keyword priors. The extractor refines these; they exist to route cheaply and to skip obvious noise."""
    text = f'{headline}\n{content[:1500]}'
    categories = [name for name, pattern in _COMPILED.items() if pattern.search(text)]
    for topic in topics or []:
        mapped = ALPHA_VANTAGE_TOPICS.get(str(topic).lower())
        if mapped and mapped not in categories:
            categories.append(mapped)
    for default in source.get('default_categories') or []:
        if default not in categories:
            categories.append(default)
    assets = [name for name, pattern in _ASSETS.items() if pattern.search(text)]
    countries = [name for name, pattern in _COUNTRIES.items() if pattern.search(text)]
    if not countries and source.get('country') and source['country'] != 'GLOBAL':
        countries = [source['country']]
    entities = [name for name, pattern in _INSTITUTIONS.items() if pattern.search(text)]
    matched = sum(1 for name, pattern in _COMPILED.items() if pattern.search(headline))
    prior = int(source.get('priority', 50)) * 0.4 + min(3, matched) * 12
    if HIGH_SIGNAL.search(headline):
        prior += 25
    if assets:
        prior += 5
    if source.get('category') in MEDIA_SOURCE_CATEGORIES and matched == 0:
        prior = min(prior, 10)
    return {'categories': categories, 'assets': assets, 'countries': countries, 'entities': entities,
            'importance_prior': int(max(0, min(100, round(prior))))}


ALPHA_VANTAGE_TOPICS = {
    'economy_macro': 'GROWTH', 'economy_monetary': 'MONETARY_POLICY', 'economy_fiscal': 'GLOBAL_GEOPOLITICAL',
    'blockchain': 'CRYPTO', 'financial_markets': 'GROWTH',
}
