"""The outlook layer: direction, the shape of the path, the weight of the evidence, and the engine's own hit rate."""
from datetime import datetime, timedelta, timezone

from app import outlook
from tests.conftest import needs_database


def horizons(now, week, months):
    return {'immediate': {'score': now, 'direction': 'NEUTRAL', 'evidence': 1.0},
            'short_term': {'score': week, 'direction': 'NEUTRAL', 'evidence': 1.0},
            'medium_term': {'score': months, 'direction': 'NEUTRAL', 'evidence': 1.0}}


def test_path_separates_a_knee_jerk_from_a_structural_move():
    assert outlook.path(horizons(-60, -40, -10)) == 'FADING'
    assert outlook.path(horizons(8, 25, 50)) == 'BUILDING'
    assert outlook.path(horizons(40, 38, 36)) == 'STEADY'
    assert outlook.path(horizons(45, 10, -30)) == 'FLIPPING'
    assert outlook.path(horizons(45, 10, -4)) == 'FADING'      # too small to call a reversal


def test_direction_comes_from_the_horizon_that_carries_the_move():
    """A slow-burning event does nothing in the first hours; calling that "no clear direction" would hide the read."""
    assert outlook.direction(horizons(0, -20, -55)) == 'BEARISH'
    assert outlook.direction(horizons(70, 40, 10)) == 'BULLISH'
    assert outlook.direction(horizons(4, -3, 2)) == 'NEUTRAL'
    assert outlook.direction({}) == 'NEUTRAL'


def test_evidence_weight():
    assert outlook.evidence(2.0) == 'EVIDENCE_SOLID'
    assert outlook.evidence(0.7) == 'EVIDENCE_FAIR'
    assert outlook.evidence(0.1) == 'EVIDENCE_THIN'


def test_us10y_says_what_up_means():
    """The one asset a reader gets backwards: a positive US10Y score is the yield, not the bond."""
    assert 'yield' in outlook.up_means('US10Y') and 'bond prices lower' in outlook.up_means('US10Y')
    assert outlook.name('XAUUSD') == 'Gold' and outlook.name('US10Y') == 'US 10-year yield'


def bias(score, drivers=(), evidence=1.0):
    return {'score': score, 'macroBias': 'BEARISH' if score < -40 else 'SLIGHT_BULLISH' if score > 0 else 'NEUTRAL',
            'horizons': {name: {'score': score, 'direction': 'NEUTRAL', 'evidence': evidence}
                         for name in outlook.HORIZONS},
            'drivers': list(drivers)}


def test_rows_keep_the_assets_with_a_read_and_name_the_rest():
    driver = {'eventId': 'e1', 'title': 'US CPI above forecast', 'importance': 95,
              'rationale': 'Real yields rise and gold pays no coupon.'}
    assets = {'XAUUSD': bias(-55, [driver]), 'BTC': bias(-3), 'USD': bias(30, [driver])}
    view = outlook.rows(assets)
    assert [row['asset'] for row in view['material']] == ['XAUUSD', 'USD']       # strongest read first
    assert 'BTC' in view['quiet']                                                # noise is named, not narrated
    gold = view['material'][0]
    assert gold['name'] == 'Gold' and gold['direction'] == 'BEARISH' and gold['path'] == 'STEADY'
    assert gold['drivers'][0]['rationale'].startswith('Real yields')
    assert gold['trackRecord'] is None


def test_rows_drop_a_driver_with_no_reason_and_cap_the_list():
    """A driver with no rationale can be counted but never printed: the section exists to give the reason."""
    silent = {'eventId': 'e0', 'title': 'Untitled', 'importance': 40, 'rationale': '   '}
    spoken = {'eventId': 'e1', 'title': 'US CPI above forecast', 'importance': 95, 'rationale': 'Real yields rise.'}
    assets = {asset: bias(-60 + index, [silent, spoken]) for index, asset in enumerate(outlook.ASSET_NAMES)}
    view = outlook.rows(assets, limit=3)
    assert len(view['material']) == 3
    assert len(view['quiet']) == len(outlook.ASSET_NAMES) - 3     # the ones over the cap are named, not dropped
    row = view['material'][0]
    assert [driver['rationale'] for driver in row['drivers']] == ['Real yields rise.']
    assert row['eventCount'] == 2


def test_prose_and_translated_round_trip_the_rationales():
    view = outlook.rows({'XAUUSD': bias(-55, [{'eventId': 'e1', 'title': 'CPI', 'importance': 95,
                                                'rationale': 'Real yields rise.'}])})
    assert outlook.prose(view) == ['Real yields rise.']
    localized = outlook.translated(view, {'Real yields rise.': 'km:Real yields rise.'})
    assert localized['material'][0]['drivers'][0]['rationale'] == 'km:Real yields rise.'
    assert view['material'][0]['drivers'][0]['rationale'] == 'Real yields rise.'   # the English payload is untouched


def test_clip_keeps_short_text_and_marks_what_it_cut():
    assert outlook.clip('short', 10) == 'short'
    assert outlook.clip('a' * 20, 10) == 'a' * 9 + '…'          # no boundary to find: cut where the limit falls
    # A published post must not end mid-word, and the trailing punctuation goes with the cut word.
    assert outlook.clip('the effect decays as the next print approaches (mock analyst)', 50) == \
        'the effect decays as the next print approaches…'
    assert outlook.clip('CPI rose 0.4% in August; inflation 3.1% vs 2.9% forecast, previous 2.8%', 70) == \
        'CPI rose 0.4% in August; inflation 3.1% vs 2.9% forecast, previous…'


@needs_database
def test_track_record_counts_only_directional_calls_and_withholds_a_thin_rate():
    from app.db import database
    now = datetime.now(timezone.utc)
    calls = ([('XAUUSD', 'CONFIRMED')] * 6 + [('XAUUSD', 'REJECTED')] * 2 + [('XAUUSD', 'FLAT')] * 3
             + [('BTC', 'CONFIRMED')] * 2)
    event_sql = ("INSERT INTO economic_events (id, event_key, event_type, title, importance) "
                 "VALUES (%s, %s, 'CPI_RELEASE', 'Track record fixture', 90) ON CONFLICT (id) DO NOTHING")
    reaction_sql = ("INSERT INTO market_reactions (event_id, asset, anchor_at, anchor_price, window_label, due_at, "
                    "price, change_pct, measured_at, expected_direction, interpretation) "
                    "VALUES (%s, %s, %s, 100, %s, %s, 101, 1.0, %s, %s, %s)")
    with database() as conn:
        conn.execute('DELETE FROM market_reactions')
        for index, (asset, interpretation) in enumerate(calls):
            # One event per call: market_reactions is unique per (event, asset, window).
            event_id = f'evt_track_{index}'
            conn.execute(event_sql, (event_id, f'track|US|{index}'))
            conn.execute(reaction_sql, (event_id, asset, now - timedelta(hours=2), ('1h', '4h', '24h')[index % 3],
                                        now - timedelta(hours=1), now - timedelta(minutes=30), 'BULLISH',
                                        interpretation))
        # Neither of these is a directional call at a window the scorecard reads, so neither may reach the
        # denominator: one is a 5-minute window (noise, deliberately excluded), the other had no expected direction.
        conn.execute(reaction_sql, ('evt_track_0', 'XAUUSD', now - timedelta(hours=2), '5m',
                                    now - timedelta(hours=1), now - timedelta(minutes=30), 'BULLISH', 'REJECTED'))
        conn.execute(reaction_sql, ('evt_track_1', 'XAUUSD', now - timedelta(hours=2), '1h',
                                    now - timedelta(hours=1), now - timedelta(minutes=30), 'NEUTRAL', 'REJECTED'))
        record = outlook.track_record(conn, days=7)
        stale = outlook.track_record(conn, days=0)
    assert record['XAUUSD'] == {'confirmed': 6, 'rejected': 2, 'flat': 3, 'checks': 8, 'days': 7, 'hitRate': 75}
    assert record['BTC']['hitRate'] is None and record['BTC']['checks'] == 2     # 2 of 2 is not a hit rate
    assert stale == {}                                                           # the measurement window is respected


def test_rows_name_a_direction_the_horizons_actually_support():
    """The 2026-09-10 brief printed "Gold - no clear direction" above +22 / +11 / +4.

    An asset clears MATERIAL on its blended score, but that blend (0.3/0.5/0.2) lands inside bias_label's +/-15
    NEUTRAL band whenever the move sits in the immediate horizon and decays. The direction has to come from the
    horizon carrying the move, which is what app.outlook.direction does and what the alert path already used.
    """
    gold = {'score': 13, 'macroBias': 'NEUTRAL',       # 22*0.3 + 11*0.5 + 4*0.2 = 12.9
            'horizons': {'immediate': {'score': 22, 'direction': 'SLIGHT_BULLISH', 'evidence': 1.0},
                         'short_term': {'score': 11, 'direction': 'NEUTRAL', 'evidence': 1.0},
                         'medium_term': {'score': 4, 'direction': 'NEUTRAL', 'evidence': 1.0}},
            'drivers': [{'eventId': 'e1', 'title': 'Trade war escalates', 'importance': 75,
                         'rationale': 'Haven bid as import prices rise.'}]}
    row = outlook.rows({'XAUUSD': gold})['material'][0]
    assert row['direction'] == 'SLIGHT_BULLISH'     # not NEUTRAL: the blend is not what the reader is shown
    assert row['path'] == 'FADING'                  # and it must not read "no clear direction, fading"
    assert row['score'] == 13                       # the blended score itself is unchanged


def test_rows_still_call_a_genuinely_flat_read_neutral():
    flat = {'score': 9, 'macroBias': 'NEUTRAL',
            'horizons': {name: {'score': 9, 'direction': 'NEUTRAL', 'evidence': 1.0} for name in outlook.HORIZONS},
            'drivers': []}
    assert outlook.rows({'SPX': flat})['material'][0]['direction'] == 'NEUTRAL'
