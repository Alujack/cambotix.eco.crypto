from datetime import datetime, timezone

from app.grounding import ungrounded_claims

BRIEF = {
    'riskRegime': 'NEUTRAL', 'globalLiquidity': 'STABLE',
    'macroRegime': {'US': {'inflation': {'state': 'NEAR_TARGET', 'trend': 'STABLE', 'score': 9, 'known': True}}},
    'assetPressure': {'USD': 24, 'XAUUSD': 15},
    'developments': [{'title': 'U.S. reveals import ban on Canadian goods', 'summary': 'The US imposes an import ban, '
                      'a stagflationary risk-off event.', 'riskRegimeImpact': 'RISK_OFF', 'relationToTrend': 'NEW_THEME'}],
    'upcoming': [{'title': 'Main Refinancing Rate', 'currency': 'EUR', 'impact': 'HIGH', 'forecast': '2.65%',
                  'previous': '2.40%'}],
    'officialHeadlines': [], 'topHeadlines': [], 'keyRisks': ['Escalation of trade tensions'],
}
# The same brief with a scheduled time on the release, which is how app.briefs builds it (a datetime).
TIMED = {**BRIEF, 'date': '2026-09-11', 'generatedAt': datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc),
         'upcoming': [{**BRIEF['upcoming'][0],
                       'scheduledAt': datetime(2026, 9, 11, 12, 15, tzinfo=timezone.utc)}]}


def test_grounded_narrative_passes():
    text = ('The regime is NEUTRAL with liquidity STABLE. The US import ban on Canadian goods is a risk-off, '
            'stagflationary development, and gold carries positive pressure into the ECB Main Refinancing Rate.')
    assert ungrounded_claims(text, BRIEF) == ['ECB']  # ECB is not in today's data; only the release title is


def test_fabricated_event_is_caught():
    """The real llama3.1:8b failure: a BOJ rate hike that appeared nowhere in the input."""
    text = ("The BOJ's decision to hike rates in Japan has been a key driver of risk-off sentiment, with the yen "
            "and JGB yields strengthening.")
    missing = ungrounded_claims(text, BRIEF)
    assert 'BOJ' in missing and 'Japan' in missing and 'JGB' in missing


def test_invented_numbers_are_caught():
    assert '7.5%' in ungrounded_claims('Inflation is running at 7.5% year over year.', BRIEF)
    assert ungrounded_claims('The forecast is 2.65% versus 2.40% previously.', BRIEF) == []


def test_empty_and_safe_text():
    assert ungrounded_claims('', BRIEF) == []
    assert ungrounded_claims('Markets are quiet today. The week ahead looks thin.', BRIEF) == []


def test_a_plural_does_not_withhold_the_narrative():
    """The 2026-09-10 brief lost its narrative to "Upcoming, Risks": SAFE_WORDS had 'risk' but not 'risks'.

    These words label a section or a reading; they assert nothing that could be fabricated.
    """
    assert ungrounded_claims('Upcoming risks are contained.', BRIEF) == []
    assert ungrounded_claims('Risks remain to the downside. Upcoming data is thin.', BRIEF) == []
    assert ungrounded_claims('Developments overnight were mixed and the Outlook is steady.', BRIEF) == []


def test_a_plural_standing_on_a_singular_in_the_data_is_grounded():
    brief = {**BRIEF, 'keyRisks': ['Escalation of the tariff dispute']}
    assert ungrounded_claims('Tariffs are the driver here.', brief) == []


def test_a_time_zone_or_unit_label_does_not_withhold_the_narrative():
    """'utc' was safe but 'GMT' was not, so "the print lands at 12:30 GMT" withheld a whole brief."""
    assert ungrounded_claims('Inflation is steady YoY while the curve moved a few BPS.', BRIEF) == []
    assert ungrounded_claims('The read is unchanged as of 06:00 GMT.', TIMED) == []


def test_a_release_time_is_grounded_only_if_the_calendar_says_so():
    """The time is a claim, not noise: the calendar's own time passes and an invented one is still caught."""
    assert ungrounded_claims('The rate decision lands at 12:15 GMT.', TIMED) == []
    # The number pattern reads a clock time as its two groups, so both halves come back as ungrounded.
    assert ungrounded_claims('The rate decision lands at 14:45 GMT.', TIMED) == ['14', '45']


def test_plural_handling_still_catches_a_fabrication():
    """The relaxation must not open a hole: an invented institution has no stem in the data either."""
    missing = ungrounded_claims('The BOJ hiked and JGB yields rose across Japan.', BRIEF)
    assert 'BOJ' in missing and 'JGB' in missing and 'Japan' in missing
