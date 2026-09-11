"""Groundedness check for model-written prose in the brief.

A fluent paragraph that asserts an event which never appeared in its input is worse than no paragraph: the brief is
delivered to Telegram and read as fact. So narrative prose is published only if the specific claims in it - proper
nouns and numbers - are present in the data the model was given. This is a publish/withhold gate, never a rewrite.
"""
import re

# Sentence-initial and common words that are capitalised without naming anything.
SAFE_WORDS = {
    'the', 'a', 'an', 'and', 'but', 'or', 'if', 'as', 'at', 'by', 'for', 'from', 'in', 'into', 'of', 'on', 'to', 'with',
    'this', 'that', 'these', 'those', 'it', 'its', 'their', 'there', 'here', 'today', 'tomorrow', 'yesterday',
    'markets', 'market', 'traders', 'investors', 'risk', 'growth', 'inflation', 'employment', 'liquidity', 'policy',
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'meanwhile', 'overall', 'however',
    'while', 'after', 'before', 'both', 'no', 'not', 'none', 'still', 'so', 'yet', 'data', 'week', 'month', 'session',
    'utc', 'gdp', 'cpi', 'ppi', 'pce', 'nfp', 'etf', 'q1', 'q2', 'q3', 'q4', 'higher', 'lower', 'stagflation',
    'neutral', 'unknown', 'stable', 'rising', 'falling', 'haven', 'equities', 'bonds', 'yields', 'crypto',
    # Words that name a section or a reading rather than a thing in the world. A model that opens a sentence with
    # "Upcoming" or "Risks" is labelling, not asserting: withholding the whole narrative over one of these lost the
    # 2026-09-10 brief its prose ("the model referred to Upcoming, Risks").
    'upcoming', 'outlook', 'calendar', 'headline', 'headlines', 'development', 'developments', 'source', 'sources',
    'asset', 'regime', 'macro', 'global', 'sentiment', 'expectations', 'conditions', 'pressure', 'direction',
    'evidence', 'confidence', 'summary', 'brief', 'across', 'against', 'through', 'between', 'given', 'should',
    'ahead', 'near', 'above', 'below', 'steady', 'mixed', 'flat', 'firmer', 'softer', 'easing', 'tightening',
    # Time zones and comparison/unit abbreviations. Capitalised, but they name no institution, country or figure -
    # the same reason 'utc' is here. A brief was withheld because the model wrote a release time "at 12:30 GMT".
    'gmt', 'est', 'edt', 'cst', 'cdt', 'pst', 'pdt', 'cet', 'cest', 'bst', 'jst', 'ict', 'aest', 'hkt', 'sgt',
    'yoy', 'qoq', 'mom', 'bps', 'pct', 'eod', 'ytd',
}


def _is_safe(token: str) -> bool:
    """SAFE_WORDS plus its regular plurals: "Risks" is as empty a claim as "risk", and listing every plural by hand
    is how "risk" ended up safe while "Risks" withheld a whole narrative."""
    word = token.lower().rstrip('.,;:')
    if word in SAFE_WORDS:
        return True
    for suffix, stem in (('ies', 'y'), ('es', ''), ('s', '')):
        if word.endswith(suffix) and word[:-len(suffix)] + stem in SAFE_WORDS:
            return True
    return False
PROPER_NOUN = re.compile(r'\b([A-Z][A-Za-z&.\-]{1,})\b')
NUMBER = re.compile(r'-?\d+(?:[.,]\d+)?%?')


def _corpus(brief: dict) -> str:
    parts = [brief.get('riskRegime', ''), brief.get('globalLiquidity', ''), str(brief.get('date') or '')]
    # The brief's own header carries its date and generation time, so prose that refers to them is grounded.
    stamp = brief.get('generatedAt')
    if stamp is not None:
        parts.append(f'{stamp:%H:%M %Y-%m-%d}' if hasattr(stamp, 'strftime') else str(stamp))
    for development in brief.get('developments') or []:
        parts += [str(development.get('title', '')), str(development.get('summary', '')),
                  str(development.get('riskRegimeImpact', '')), str(development.get('relationToTrend', ''))]
    for release in brief.get('upcoming') or []:
        parts += [str(release.get('title', '')), str(release.get('currency', '')), str(release.get('forecast') or ''),
                  str(release.get('previous') or '')]
        # When a release is due is a checkable claim, so it belongs in the ground rather than being exempted from
        # the check: "CPI lands at 12:30" is then grounded, while an invented "14:00" is still caught. Without this
        # the calendar's times were nowhere in the corpus and any time at all withheld the narrative.
        when = release.get('scheduledAt')
        if when is not None:
            parts.append(f'{when:%H:%M %Y-%m-%d}' if hasattr(when, 'strftime') else str(when))
    for headline in (brief.get('officialHeadlines') or []) + (brief.get('topHeadlines') or []):
        parts += [str(headline.get('headline', '')), str(headline.get('source', ''))]
    parts += [str(risk) for risk in brief.get('keyRisks') or []]
    for region, dims in (brief.get('macroRegime') or {}).items():
        parts.append(str(region))
        for dimension, value in dims.items():
            parts += [dimension.replace('_', ' '), str(value.get('state', '')), str(value.get('trend', '')),
                      str(value.get('score', ''))]
    for asset, score in (brief.get('assetPressure') or {}).items():
        parts += [asset, str(score), str(abs(int(score))) if isinstance(score, int) else '']
    # The per-asset outlook is in the model's input too, so its instrument names, driving events and the analyst's
    # own rationales are legitimate ground for the narrative to stand on.
    outlook = brief.get('assetOutlook') or {}
    parts += list(outlook.get('quiet') or [])
    for row in outlook.get('material') or []:
        parts += [str(row.get('asset', '')), str(row.get('name', '')), str(row.get('upMeans', '')),
                  str(row.get('direction', '')), str(row.get('score', '')),
                  str(abs(int(row['score']))) if isinstance(row.get('score'), int) else '']
        for horizon in (row.get('horizons') or {}).values():
            parts += [str(horizon.get('score', '')), str(abs(int(horizon['score'])))
                      if isinstance(horizon.get('score'), int) else '']
        for driver in row.get('drivers') or []:
            parts += [str(driver.get('title', '')), str(driver.get('rationale', '')), str(driver.get('importance', ''))]
    return ' '.join(parts).lower()


def ungrounded_claims(text: str, brief: dict) -> list[str]:
    """Proper nouns and numbers in `text` that do not appear anywhere in the data the model was given."""
    if not text:
        return []
    corpus = _corpus(brief)
    missing = []
    for token in PROPER_NOUN.findall(text):
        if len(token) < 3 or _is_safe(token):
            continue
        word = token.lower().rstrip('.,;:')
        # A plural standing on a singular in the data is grounded: the corpus says "tariff", the prose says
        # "Tariffs". Fabrications ("BOJ", "JGB") have no stem in the corpus either way and are still caught.
        stems = {word} | {word[:-len(s)] + stem for s, stem in (('ies', 'y'), ('es', ''), ('s', ''))
                 if word.endswith(s) and len(word) > len(s) + 2}
        if not any(stem in corpus for stem in stems):
            missing.append(token)
    for number in NUMBER.findall(text):
        if number.strip('%') in {'0', '1', '2', '3', '4', '5', '24', '48'}:
            continue          # counts and horizons the prose may legitimately compute
        if number.lower() not in corpus and number.strip('%').lower() not in corpus:
            missing.append(number)
    return list(dict.fromkeys(missing))
