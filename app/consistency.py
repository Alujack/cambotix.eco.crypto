"""Self-consistency checks on an analysis.

These never modify what the model said. They record where its own outputs disagree, so a weak model's analyses can be
discounted downstream instead of silently trusted. Provider-independent: a strong model simply produces no flags.
"""
RISK_ASSETS = {'SPX', 'NASDAQ', 'BTC', 'ETH'}
HAVEN_ASSETS = {'XAUUSD'}
THRESHOLD = 15  # ignore scores inside the noise band


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
