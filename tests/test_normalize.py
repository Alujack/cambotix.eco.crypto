from datetime import datetime, timezone

from app.normalize import article_id, classify, clean_text, content_hash, parse_datetime

SOURCE_OFFICIAL = {'key': 'bls_cpi', 'category': 'official_statistics', 'country': 'US', 'priority': 100,
                   'default_categories': ['INFLATION']}
SOURCE_MEDIA = {'key': 'coindesk', 'category': 'crypto_media', 'country': 'GLOBAL', 'priority': 55, 'default_categories': ['CRYPTO']}


def test_parse_datetime_formats():
    assert parse_datetime('2026-09-09T14:30:00Z') == datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)
    assert parse_datetime('2026-09-09T10:30:00-04:00') == datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)
    assert parse_datetime('20260909T143000') == datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)
    assert parse_datetime('Wed, 09 Sep 2026 14:30:00 GMT') == datetime(2026, 9, 9, 14, 30, tzinfo=timezone.utc)
    assert parse_datetime('not a date') is None
    assert parse_datetime(None) is None


def test_content_hash_is_stable_and_source_scoped():
    a = content_hash('fed_press_monetary', None, 'https://x/1', 'Fed holds rates')
    assert a == content_hash('fed_press_monetary', None, 'https://x/1', 'Different headline, same url')
    assert a != content_hash('cnbc_economy', None, 'https://x/1', 'Fed holds rates')
    assert content_hash('s', None, None, 'Fed  Holds Rates') == content_hash('s', None, None, 'fed holds rates')
    assert article_id(a).startswith('art_') and len(article_id(a)) == 24


def test_clean_text_strips_html():
    assert clean_text('<p>CPI &amp; PCE rose <b>0.4%</b></p>\n\n more') == 'CPI & PCE rose 0.4% more'


def test_classify_official_release_gets_high_prior():
    tags = classify('Consumer Price Index - August 2026', 'The CPI rose 0.4 percent, BLS reported.', SOURCE_OFFICIAL)
    assert 'INFLATION' in tags['categories']
    assert 'US' in tags['countries']
    assert 'BLS' in tags['entities']
    assert tags['importance_prior'] >= 60


def test_classify_irrelevant_media_item_is_low_prior():
    tags = classify('Top 5 NFT games to play this weekend', 'Fun for the whole family.', SOURCE_MEDIA)
    assert tags['importance_prior'] <= 10


def test_classify_relevant_crypto_item_tags_assets():
    tags = classify('SEC approves spot Ethereum ETF options', 'Coinbase and Kraken listed the products.', SOURCE_MEDIA)
    assert 'CRYPTO' in tags['categories']
    assert 'ETH' in tags['assets']
    assert tags['importance_prior'] >= 40


def test_alpha_vantage_topics_map_to_categories():
    tags = classify('Markets wrap', 'nothing specific', {'key': 'alphavantage_news', 'category': 'financial_media',
                                                        'country': 'GLOBAL', 'priority': 55, 'default_categories': []},
                    topics=['economy_monetary'])
    assert 'MONETARY_POLICY' in tags['categories']


def test_normalize_countries_maps_names_to_iso2():
    from app.normalize import normalize_countries
    assert normalize_countries(['Canada', 'United States', 'canada']) == ['CA', 'US']
    assert normalize_countries(['U.S.', 'Euro area', 'UK', 'Iran']) == ['US', 'EU', 'GB', 'IR']
    assert normalize_countries(['World', 'Middle East']) == ['GLOBAL']
    assert normalize_countries(['Freedonia']) == ['GLOBAL']   # unknown long form degrades, never leaks a bad key
    assert normalize_countries([]) == [] and normalize_countries(None) == []
    assert normalize_countries(['us', ' ca ']) == ['US', 'CA']
