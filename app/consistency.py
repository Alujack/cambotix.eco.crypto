"""Self-consistency checks on an analysis.

These never modify what the model said. They record where its own outputs disagree, so a weak model's analyses can be
discounted downstream instead of silently trusted. Provider-independent: a strong model simply produces no flags.
"""
RISK_ASSETS = {'SPX', 'NASDAQ', 'BTC', 'ETH'}
HAVEN_ASSETS = {'XAUUSD'}
THRESHOLD = 15  # ignore scores inside the noise band
# Assets that cannot move the same way with conviction, because one is quoted in the other. EURUSD rising *is* the
# dollar falling against the euro, and the euro is the largest weight in any broad dollar measure - so "dollar
# stronger +40" alongside "euro stronger against the dollar +34" is the model contradicting itself, not a nuance.
# Both scores must clear the noise band before this is called: a flat leg is a non-committal, not a contradiction.
INVERSE_PAIRS = [('USD', 'EURUSD')]


def check(analysis) -> list[dict]:
    flags = []
    impacts = {impact.asset: impact for impact in analysis.asset_impacts}
    regime = analysis.risk_regime_impact
    for asset in sorted(RISK_ASSETS & impacts.keys()):
        score = impacts[asset].immediate.score
        if regime == 'RISK_OFF' and score > THRESHOLD:
            flags.append({'code': 'risk_regime_sign', 'asset': asset,
                          'detail': f'risk_regime_impact is RISK_OFF but {asset} immediate score is {score:+d}'})
        elif regime == 'RISK_ON' and score < -THRESHOLD:
            flags.append({'code': 'risk_regime_sign', 'asset': asset,
                          'detail': f'risk_regime_impact is RISK_ON but {asset} immediate score is {score:+d}'})
    for asset in sorted(HAVEN_ASSETS & impacts.keys()):
        score = impacts[asset].immediate.score
        if regime == 'RISK_OFF' and score < -THRESHOLD:
            flags.append({'code': 'haven_sign', 'asset': asset,
                          'detail': f'risk_regime_impact is RISK_OFF but haven {asset} immediate score is {score:+d}'})
    for base, quote in INVERSE_PAIRS:
        if base in impacts and quote in impacts:
            base_score, quote_score = impacts[base].immediate.score, impacts[quote].immediate.score
            if min(abs(base_score), abs(quote_score)) > THRESHOLD and (base_score > 0) == (quote_score > 0):
                flags.append({'code': 'inverse_pair_sign', 'asset': quote,
                              'detail': f'{base} immediate score is {base_score:+d} and {quote} is {quote_score:+d}, '
                                        f'but {quote} moves inversely to {base}'})
    # Zero on every horizon is a non-commitment, not a judgement: the analysis listed the asset as affected.
    for asset, impact in sorted(impacts.items()):
        if impact.immediate.score == 0 and impact.short_term.score == 0 and impact.medium_term.score == 0:
            flags.append({'code': 'zero_all_horizons', 'asset': asset,
                          'detail': f'{asset} is listed as affected but scored 0 on every horizon'})
    # A directional rationale paired with a zero score means the model hedged instead of committing.
    for asset, impact in sorted(impacts.items()):
        text = (impact.rationale or '').lower()
        if impact.immediate.score == 0 and any(word in text for word in ('rise', 'rises', 'fall', 'falls', 'higher',
                                                                          'lower', 'strengthen', 'weaken')):
            flags.append({'code': 'zero_with_direction', 'asset': asset,
                          'detail': f'{asset} scored 0 while its rationale states a direction'})
    # One rationale copied across every asset carries no per-asset reasoning.
    rationales = [(impact.rationale or '').strip() for impact in analysis.asset_impacts]
    if len(rationales) >= 3 and len(set(rationales)) == 1:
        flags.append({'code': 'duplicate_rationale', 'asset': None,
                      'detail': f'the same rationale is repeated for all {len(rationales)} assets'})
    if analysis.summary.strip().lower() == (analysis.what_happened or '').strip().lower():
        flags.append({'code': 'summary_repeats_event', 'asset': None, 'detail': 'summary duplicates what_happened'})
    return flags
