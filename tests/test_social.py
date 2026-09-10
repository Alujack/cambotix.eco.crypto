"""Publishable posts: what a channel or page reader gets, and what they must never get."""
from datetime import datetime, timezone

from app import i18n, social, telegram
from app.ai import mock_analyze
from app.macro_state import DIMENSIONS

# Terms a post must never reach for. The engine analyses economic impact; the moment a public post says "buy" it is
# selling a trade instead, which is exactly what this project does not do.
TRADING_TERMS = ['buy', 'sell', 'long ', 'short position', 'entry', 'stop loss', 'stop-loss', 'take profit',
                 'target price', 'price target', 'leverage', 'lot size', 'portfolio allocation']


def outlook_row(**over):
    row = {'asset': 'XAUUSD', 'name': 'Gold', 'upMeans': 'gold price higher', 'score': -38,
           'direction': 'SLIGHT_BEARISH', 'path': 'FADING', 'evidence': 'EVIDENCE_SOLID', 'eventCount': 4,
           'horizons': {'immediate': {'score': -52, 'direction': 'BEARISH'},
                        'short_term': {'score': -40, 'direction': 'BEARISH'},
                        'medium_term': {'score': -14, 'direction': 'SLIGHT_BEARISH'}},
           'drivers': [{'eventId': 'evt_cpi', 'title': 'US CPI above the consensus', 'importance': 95,
                        'rationale': 'Real yields rise and gold pays no coupon to offset that.'}],
           'trackRecord': {'confirmed': 12, 'rejected': 5, 'flat': 2, 'checks': 17, 'days': 30, 'hitRate': 71}}
    row.update(over)
    return row


def brief(**over):
    regime = {region: {dim: {'state': 'UNKNOWN', 'trend': 'STABLE', 'score': 0, 'known': False} for dim in dims}
              for region, dims in DIMENSIONS.items()}
    regime['US']['inflation'] = {'state': 'ABOVE_TARGET', 'trend': 'RISING', 'score': 35, 'known': True}
    regime['GLOBAL']['risk_appetite'] = {'state': 'RISK_OFF', 'trend': 'FALLING', 'score': -22, 'known': True}
    value = {
        'kind': 'daily', 'date': '2026-09-09', 'generatedAt': datetime(2026, 9, 9, 6, tzinfo=timezone.utc),
        'macroRegime': regime, 'riskRegime': 'RISK_OFF', 'globalLiquidity': 'DETERIORATING',
        'assetPressure': {'USD': 41, 'XAUUSD': -38}, 'assetOutlook': {'material': [outlook_row()], 'quiet': ['ETH']},
        'developments': [{'eventId': 'evt_cpi', 'title': 'US CPI above the consensus', 'importance': 95,
                          'summary': 'US headline CPI printed 3.4% against a 3.1% consensus.',
                          'riskRegimeImpact': 'RISK_OFF', 'relationToTrend': 'CONFIRMS'}],
        'upcoming': [{'currency': 'USD', 'title': 'Core PCE Price Index m/m', 'impact': 'HIGH',
                      'scheduledAt': datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
                      'forecast': '0.3%', 'previous': '0.2%'},
                     {'currency': 'EUR', 'title': 'German factory orders', 'impact': 'MEDIUM',
                      'scheduledAt': datetime(2026, 9, 11, 6, tzinfo=timezone.utc), 'forecast': None, 'previous': None}],
        'officialHeadlines': [], 'topHeadlines': [],
        'keyRisks': ['A cooler core print on Friday would undo most of the repricing.'],
        'narrative': 'The third upside CPI surprise in a row moved the policy path. Gold is the clearest casualty. '
                     'Friday is the test.',
        'pipeline': {'received24h': 341, 'sources24h': 18, 'queued': 12, 'extracted24h': 280, 'analyzed24h': 6,
                     'aiConfigured': True, 'model': 'mock', 'flagged24h': 0, 'analysesTotal24h': 6},
    }
    value.update(over)
    return value


def cpi_analysis():
    analysis = mock_analyze({'event': {'title': 'US CPI 3.4% y/y', 'categories': ['INFLATION'],
                                       'facts': {'surprise': 'ABOVE_EXPECTATIONS'}}})
    analysis.summary = ('US headline CPI printed 3.4% year over year against a 3.1% consensus. It is the third '
                        'consecutive upside surprise.')
    return analysis


def cpi_event(**over):
    event = {'id': 'evt_cpi', 'title': 'US CPI 3.4% y/y vs 3.1% expected', 'importance': 95,
             'categories': ['INFLATION'], 'article_count': 7}
    event.update(over)
    return event


def body(text: str, lang: str = 'en') -> str:
    """The post without its disclaimer - the one line that is allowed to name what the engine does not do."""
    return text.replace(i18n.labels(lang)['social_disclaimer'], '').lower()


def test_daily_post_leads_with_the_story_then_says_what_each_market_does():
    post = social.daily_post(brief(), 'telegram')
    text = post['text']
    assert text.startswith('<b>🌍 GLOBAL MACRO · 2026-09-09 · 06:00 UTC</b>')
    assert 'The third upside CPI surprise in a row moved the policy path.' in text
    assert '🟡 US inflation — above target and rising ↑ (+35)' in text
    assert 'Overall: risk appetite is risk-off, and global liquidity is deteriorating.' in text
    # A direction in words, the path across three horizons, what the sign means, and the mechanism.
    assert '🔽 Gold (XAUUSD) — leaning lower, strongest now and fading over the following weeks' in text
    assert 'Next 4h -52 · 1-5 days -40 · 2-8 weeks -14 · (+ = gold price higher)' in text
    assert 'Why: Real yields rise and gold pays no coupon to offset that.' in text
    # Only HIGH-impact releases are news; the MEDIUM one stays out of the post.
    assert '🇺🇸 Fri 12:30 UTC — USD Core PCE Price Index m/m · fcst 0.3% · prev 0.2%' in text
    assert 'German factory orders' not in text
    assert 'A cooler core print on Friday' in text
    assert post['parseMode'] == 'HTML' and post['chars'] == len(text)


def test_a_post_carries_the_engines_evidence_instead_of_its_plumbing():
    """A stranger is served by how much was read and how past calls did, not by model names and queue depths."""
    text = social.daily_post(brief(), 'telegram')['text']
    assert '📡 Read from 341 items across 18 sources in the last 24h.' in text
    assert "🎯 The engine's past calls on XAUUSD: 12 of 17 confirmed by the actual price move (30d)." in text
    assert 'mock' not in text and 'queued' not in text and 'evt_cpi' not in text


def test_an_unmeasured_track_record_is_not_claimed():
    """app.outlook withholds a hit rate below its minimum number of checks; a post must not imply one anyway."""
    thin = brief(assetOutlook={'material': [outlook_row(trackRecord={'confirmed': 2, 'checks': 3, 'days': 30,
                                                                     'hitRate': None})], 'quiet': []})
    assert '🎯' not in social.daily_post(thin, 'telegram')['text']
    assert '🎯' not in social.daily_post(brief(assetOutlook={'material': [outlook_row(trackRecord=None)],
                                                            'quiet': []}), 'telegram')['text']


def test_no_post_ever_gives_trading_advice():
    posts = [social.daily_post(brief(), platform)['text'] for platform in social.PLATFORMS]
    posts += [social.event_post(cpi_event(), cpi_analysis(), platform)['text'] for platform in social.PLATFORMS]
    for text in posts:
        assert 'not trading advice' in text
        assert [term for term in TRADING_TERMS if term in body(text)] == []


def test_the_lede_is_prose_that_already_passed_a_gate():
    """Only the brief's grounded narrative or an analyst's own summary - this module writes no prose of its own."""
    assert 'moved the policy path' in social.daily_post(brief(), 'facebook')['text']
    withheld = social.daily_post(brief(narrative=None), 'facebook')['text']
    assert 'US headline CPI printed 3.4% against a 3.1% consensus.' in withheld
    quiet = social.daily_post(brief(narrative=None, developments=[]), 'facebook')['text']
    assert 'THE STATE OF PLAY' in quiet and 'not trading advice' in quiet


def test_facebook_gets_plain_text_and_telegram_gets_html():
    plain, marked = social.daily_post(brief(), 'facebook'), social.daily_post(brief(), 'telegram')
    assert plain['parseMode'] is None and '<' not in plain['text'] and '&' not in plain['text']
    assert marked['parseMode'] == 'HTML' and '<b>💥 WHAT IT MEANS FOR MARKETS</b>' in marked['text']
    # Same words either way: a reader on one platform is not reading a different analysis.
    assert plain['text'] == marked['text'].replace('<b>', '').replace('</b>', '')


def test_a_telegram_post_is_escaped_and_fits_one_message():
    post = social.event_post(cpi_event(title='US CPI <hot> & sticky'), cpi_analysis(), 'telegram')
    assert post['text'].startswith('<b>🚨 BREAKING · US CPI &lt;hot&gt; &amp; sticky</b>')
    assert '<hot>' not in post['text']
    assert post['chars'] <= social.PLATFORMS['telegram']['limit'] < telegram.CHUNK


def test_event_post_reads_as_breaking_news():
    text = social.event_post(cpi_event(), cpi_analysis(), 'facebook', article_count=7)['text']
    assert text.startswith('🚨 BREAKING · US CPI 3.4% y/y vs 3.1% expected')
    assert 'US headline CPI printed 3.4% year over year' in text
    assert 'Inflation hotter · Growth neutral · Liquidity tighter · Fed more hawkish · overall risk-off' in text
    assert '🔺 US dollar (USD) — expected higher' in text and '🔻 Gold (XAUUSD) — expected lower' in text
    # The chain is what the engine has and a headline does not: how the event reaches the asset.
    assert 'Inflation print differs from consensus → Market re-prices the Fed path' in text
    assert 'confidence 70' in text and '7 source item(s)' in text and 'moderate evidence' in text
    assert text.count('🔺') + text.count('🔻') + text.count('🔼') + text.count('🔽') == social.POST_ASSETS


def test_a_market_the_event_does_not_move_is_left_out():
    """"USD +0 · +0 · +0" is filler in a post: the analyst's own NEUTRAL band is the bar for being news."""
    analysis = cpi_analysis()
    for impact in analysis.asset_impacts:
        for horizon in (impact.immediate, impact.short_term, impact.medium_term):
            horizon.score = 0
    text = social.event_post(cpi_event(), analysis, 'facebook')['text']
    assert 'WHAT IT MEANS FOR MARKETS' not in text and '(USD)' not in text
    # The read, the chain and the disclaimer still carry the post.
    assert 'Inflation hotter' in text and 'HOW IT TRAVELS' in text and 'not trading advice' in text


def test_the_badge_follows_importance():
    analysis = cpi_analysis()
    for importance, badge in ((95, '🚨 BREAKING'), (82, '⚠️ MARKET ALERT'), (55, '📰 MACRO UPDATE')):
        text = social.event_post(cpi_event(importance=importance), analysis, 'facebook')['text']
        assert text.startswith(badge)
    # The numeric importance is an engine detail; the badge is what a reader needs.
    assert 'importance' not in body(social.event_post(cpi_event(), analysis, 'facebook')['text'])


def test_the_economic_read_is_worded_once_for_both_renderings():
    """The private alert and the public post must not drift into two different readings of one analysis."""
    analysis = cpi_analysis()
    reads = ' · '.join(social.economic_read(analysis, 'en'))
    assert reads in social.event_post(cpi_event(), analysis, 'facebook')['text']
    assert reads in telegram.format_event_alert(cpi_event(), analysis, 7)


def test_hashtags_are_capped_and_keep_the_standing_tags():
    tags = social.hashtags(assets=['XAUUSD', 'BTC', 'USD', 'US10Y', 'SPX', 'OIL'], categories=['INFLATION'],
                           dimensions=['liquidity'], banks=['fed'])
    assert len(tags) == social.MAX_TAGS and tags[0] == '#Inflation'
    assert tags[-len(social.BASE_TAGS):] == social.BASE_TAGS
    assert social.hashtags() == social.BASE_TAGS
    assert social.hashtags(assets=['NOPE'], categories=['NOPE']) == social.BASE_TAGS


def test_a_post_that_would_overflow_gives_up_its_least_important_sections(monkeypatch):
    """Every line is clipped, so overflow is rare - but when it happens the calendar goes and the market read stays."""
    full = social.daily_post(brief(), 'facebook')
    budget = full['chars'] - 40
    monkeypatch.setitem(social.PLATFORMS['facebook'], 'limit', budget)
    trimmed = social.daily_post(brief(), 'facebook')
    assert trimmed['chars'] <= budget
    assert 'ON THE CALENDAR' not in trimmed['text'] and 'Core PCE' not in trimmed['text']
    assert 'WHAT IT MEANS FOR MARKETS' in trimmed['text'] and 'Gold (XAUUSD)' in trimmed['text']
    # The read, the credit line and the tags are the post: they are never given up, even to make a hard limit.
    monkeypatch.setitem(social.PLATFORMS['facebook'], 'limit', 200)
    squeezed = social.daily_post(brief(), 'facebook')['text']
    assert 'Gold (XAUUSD)' in squeezed and 'not trading advice' in squeezed and '#Gold' in squeezed
    assert 'THE STATE OF PLAY' not in squeezed and 'WHAT WOULD CHANGE THIS' not in squeezed


def test_an_unknown_platform_is_refused_rather_than_guessed():
    import pytest
    with pytest.raises(ValueError, match='unknown platform'):
        social.daily_post(brief(), 'myspace')


def test_khmer_post_localizes_the_words_and_keeps_tickers_and_hashtags(monkeypatch):
    monkeypatch.setattr(social.i18n, 'translate', lambda texts, lang: [f'km:{t}' for t in texts])
    text = social.daily_post(brief(), 'facebook', 'km')['text']
    assert i18n.KM_LABELS['social_markets'] in text and i18n.KM_LABELS['social_state'] in text
    assert f"{i18n.KM_ASSETS['XAUUSD']} (XAUUSD)" in text and i18n.KM_OUTLOOK['SLIGHT_BEARISH'] in text
    assert i18n.KM_STATES['inflation'][1] in text and 'ABOVE_TARGET' not in text
    assert '-52' in text and '(+ = ' + i18n.KM_UP_MEANS['XAUUSD'] + ')' in text
    # Hashtags are how a reader finds the feed, so they stay Latin-script in every language.
    assert '#Gold #Inflation' in text and i18n.KM_LABELS['social_disclaimer'] in text
    # The daily post is built from an already-localized brief (app.briefs), so it translates nothing itself.
    assert 'km:' not in text

    event = social.event_post(cpi_event(), cpi_analysis(), 'facebook', 'km', 7)['text']
    assert event.startswith(i18n.KM_LABELS['social_breaking']) and 'km:' in event
    assert i18n.KM_LABELS['social_chain'] in event and f"Fed {i18n.KM_ENUMS['MORE_HAWKISH']}" in event
