"""Display language for delivered text. English stays the storage language.

Two layers, deliberately different in kind:
  - labels: headings, field names and the enum vocabulary come from the static tables below - no model involved, so
    there is nothing to invent and nothing to fall back from
  - prose: the engine's own model-written text (brief narrative, event summaries, key risks, causal chain) goes
    through one translation call, and falls back to the English text whenever that call is unavailable or fails

Nothing here touches the database, the intelligence API or app.consistency: the gold/forex/crypto engines read English
enums, and app.grounding finds a model's claims by matching capitalised proper nouns - which Khmer script does not
have, so a Khmer narrative would pass that gate blind. Prose is therefore gated in English (app.briefs._narrative) and
translated only on the way out. Verbatim source text - headlines, event titles, source names - is left as published.
"""
import logging

from app import ai
from app.config import anthropic_configured
from app.macro_state import LABELS as STATE_VOCABULARY
from app.outlook import ASSET_NAMES as EN_ASSETS, UP_MEANS as EN_UP_MEANS

log = logging.getLogger('eco.i18n')
LANGUAGE_NAMES = {'en': 'English', 'km': 'Khmer (ភាសាខ្មែរ)'}

# ---- labels: the fixed furniture of a brief or an alert ----------------------------------------------------------
EN_LABELS = {
    'brief_title': '🌍 GLOBAL MACRO BRIEF',
    'pipeline_line': 'Pipeline: {received} items from {sources} sources in 24h · {queued} queued · '
                     '{analyzed} event(s) analyzed · analyst {model}',
    'flagged': '⚠ {flagged}/{total} analyses have self-consistency flags (GET /analysis-quality) — treat their '
               'scores with caution.',
    'ai_off': 'AI analysis is OFF — configure AI_PROVIDER (ollama / anthropic) to populate regime, developments and '
              'asset pressure.',
    'macro_regime': 'MACRO REGIME',
    'unknown_dims': '⚪ {n} dimension(s) not yet informed by any analyzed event',
    'no_macro_state': '⚪ No macro state yet — the first analyzed events populate this section.',
    'risk_liquidity': 'Overall: risk appetite is {risk}, and global liquidity is {liquidity}.',
    'narrative_withheld': 'Narrative withheld: the model referred to {tokens}, which is not in today\'s data. '
                          'Sections below are computed from the database.',
    'upcoming': 'HIGH IMPACT NEXT 48H',
    'none_scheduled': '(none scheduled)',
    'forecast': 'fcst',
    'previous': 'prev',
    'developments': 'MAJOR DEVELOPMENTS',
    'analysis_pending': '(analysis pending — AI provider not configured)',
    'no_threshold_events': '(no event reached the analysis threshold in the last 24h)',
    'official_sources': 'OFFICIAL SOURCES (24H)',
    'top_headlines': 'TOP HEADLINES (24H, by keyword signal)',
    'asset_outlook': 'WHAT THIS MEANS FOR EACH MARKET',
    'outlook_headline': '{name} ({asset}) — {direction}, {path}',
    'outlook_horizons': 'Next 4h {now}  ·  1-5 days {week}  ·  2-8 weeks {months}   ({meaning})',
    'outlook_why': 'Why',
    'outlook_driver': 'from "{title}" (importance {importance})',
    'outlook_evidence': 'Evidence {evidence}, from {n} analyzed event(s)',
    'outlook_track': "the engine's past calls here: {hit} of {total} confirmed by the actual price move ({days}d)",
    'outlook_quiet': 'Flat, nothing to act on: {assets}',
    'all_neutral': '(no analyzed event has given any asset a direction yet)',
    'key_risks': 'KEY RISKS',
    'none_recorded': '(none recorded)',
    'importance': 'importance',
    'read_inflation': 'Inflation',
    'read_growth': 'Growth',
    'read_liquidity': 'Liquidity',
    'read_risk': 'overall',
    'market_impact': 'What this means for each market',
    'alert_horizons': 'next 4h {now} · 1-5 days {week} · 2-8 weeks {months}',
    'chain': 'Chain',
    'confidence': 'confidence',
    'trend_relation': '{relation}',
    'evidence': '{evidence} evidence',
    'source_items': '{n} source item(s)',
    'event_tail': 'event {id} · {stamp} UTC',
    # ---- public posts (app.social): a channel or page reader gets news, not the engine's internals ----------
    'social_daily_title': '🌍 GLOBAL MACRO',
    'social_state': '📊 THE STATE OF PLAY',
    'social_markets': '💥 WHAT IT MEANS FOR MARKETS',
    'social_read': '🧭 THE ECONOMIC READ',
    'social_chain': '🔗 HOW IT TRAVELS',
    'social_calendar': '📅 ON THE CALENDAR · NEXT 48H',
    'social_risks': '⚠️ WHAT WOULD CHANGE THIS',
    'social_horizons': 'Next 4h {now} · 1-5 days {week} · 2-8 weeks {months} · (+ = {meaning})',
    'social_evidence': '📡 Read from {items} items across {sources} sources in the last 24h.',
    'social_track': "🎯 The engine's past calls on {asset}: {hit} of {total} confirmed by the actual price move "
                    '({days}d).',
    'social_stamp': '🕒 {stamp} UTC',
    'social_breaking': '🚨 BREAKING',
    'social_alert': '⚠️ MARKET ALERT',
    'social_update': '📰 MACRO UPDATE',
    'social_disclaimer': 'ℹ️ Economic impact analysis — not trading advice, no positions and no price targets.',
    'telegram_test': '✅ Cambotix Economic Intelligence Engine connected.\n'
                     'Daily macro brief arrives at 06:00 UTC; high-impact event alerts as they are analyzed.',
    'machine_translated': 'analysis machine-translated from English',
}

KM_LABELS = {
    'brief_title': '🌍 ព្រឹត្តិបត្រម៉ាក្រូសកល',
    'pipeline_line': 'បណ្ដាញទិន្នន័យ៖ ទទួល {received} ព័ត៌មាន ពី {sources} ប្រភព ក្នុង 24 ម៉ោង · រង់ចាំ {queued} · '
                     'វិភាគ {analyzed} ព្រឹត្តិការណ៍ · អ្នកវិភាគ {model}',
    'flagged': '⚠ ការវិភាគ {flagged}/{total} មានសញ្ញាផ្ទុយគ្នាក្នុងខ្លួនឯង (GET /analysis-quality) — '
               'សូមប្រុងប្រយ័ត្នចំពោះពិន្ទុទាំងនោះ។',
    'ai_off': 'ការវិភាគដោយ AI បិទ — សូមកំណត់ AI_PROVIDER (ollama / anthropic) ដើម្បីបំពេញសភាពការណ៍ ការវិវត្ត '
              'និងសម្ពាធលើទ្រព្យ។',
    'macro_regime': 'សភាពការណ៍ម៉ាក្រូ',
    'unknown_dims': '⚪ មាន {n} វិមាត្រ ដែលមិនទាន់មានព្រឹត្តិការណ៍វិភាគណាមួយបញ្ជាក់',
    'no_macro_state': '⚪ មិនទាន់មានសភាពការណ៍ម៉ាក្រូ — ព្រឹត្តិការណ៍វិភាគដំបូងនឹងបំពេញផ្នែកនេះ។',
    'risk_liquidity': 'សរុប៖ ចំណង់ហានិភ័យ {risk} ហើយសន្ទនីយភាពសកល {liquidity}។',
    'narrative_withheld': 'អត្ថបទសង្ខេបត្រូវបានទប់ទុក៖ គំរូបានលើកឡើងពី {tokens} ដែលមិនមានក្នុងទិន្នន័យថ្ងៃនេះ។ '
                          'ផ្នែកខាងក្រោមគណនាចេញពីមូលដ្ឋានទិន្នន័យ។',
    'upcoming': 'ព្រឹត្តិការណ៍ឥទ្ធិពលខ្ពស់ ក្នុង 48 ម៉ោងខាងមុខ',
    'none_scheduled': '(មិនមានកម្មវិធីកំណត់)',
    'forecast': 'ព្យាករ',
    'previous': 'លើកមុន',
    'developments': 'ការវិវត្តសំខាន់ៗ',
    'analysis_pending': '(រង់ចាំការវិភាគ — មិនបានកំណត់ក្រុមហ៊ុនផ្ដល់ AI)',
    'no_threshold_events': '(គ្មានព្រឹត្តិការណ៍ណាឈានដល់កម្រិតវិភាគក្នុង 24 ម៉ោងចុងក្រោយ)',
    'official_sources': 'ប្រភពផ្លូវការ (24 ម៉ោង)',
    'top_headlines': 'ព័ត៌មានសំខាន់ៗ (24 ម៉ោង តាមសញ្ញាពាក្យគន្លឹះ)',
    'asset_outlook': 'អត្ថន័យសម្រាប់ទីផ្សារនីមួយៗ',
    'outlook_headline': '{name} ({asset}) — {direction}, {path}',
    'outlook_horizons': 'ក្នុង 4 ម៉ោង {now}  ·  1-5 ថ្ងៃ {week}  ·  2-8 សប្ដាហ៍ {months}   ({meaning})',
    'outlook_why': 'មូលហេតុ',
    'outlook_driver': 'ចេញពី «{title}» (សំខាន់ {importance})',
    'outlook_evidence': 'ភស្តុតាង {evidence} ពីព្រឹត្តិការណ៍វិភាគ {n}',
    'outlook_track': 'ការទាយពីមុនលើទ្រព្យនេះ៖ {hit} ក្នុង {total} ត្រូវបានបញ្ជាក់ដោយចលនាថ្លៃពិត ({days} ថ្ងៃ)',
    'outlook_quiet': 'គ្មានទិសដៅច្បាស់ មិនចាំបាច់ធ្វើអ្វី៖ {assets}',
    'all_neutral': '(មិនទាន់មានព្រឹត្តិការណ៍វិភាគណាផ្ដល់ទិសដៅដល់ទ្រព្យណាមួយ)',
    'key_risks': 'ហានិភ័យសំខាន់ៗ',
    'none_recorded': '(មិនមានកំណត់ត្រា)',
    'importance': 'សំខាន់',
    'read_inflation': 'អតិផរណា',
    'read_growth': 'កំណើន',
    'read_liquidity': 'សន្ទនីយភាព',
    'read_risk': 'ជាទូទៅ',
    'market_impact': 'អត្ថន័យសម្រាប់ទីផ្សារនីមួយៗ',
    'alert_horizons': 'ក្នុង 4 ម៉ោង {now} · 1-5 ថ្ងៃ {week} · 2-8 សប្ដាហ៍ {months}',
    'chain': 'ខ្សែសង្វាក់',
    'confidence': 'ទំនុកចិត្ត',
    'trend_relation': '{relation}',
    'evidence': '{evidence}',
    'source_items': 'ព័ត៌មានប្រភព {n}',
    'event_tail': 'ព្រឹត្តិការណ៍ {id} · {stamp} UTC',
    # ---- public posts (app.social) ---------------------------------------------------------------------------
    'social_daily_title': '🌍 ស្ថានភាពម៉ាក្រូសកល',
    'social_state': '📊 ស្ថានភាពបច្ចុប្បន្ន',
    'social_markets': '💥 អត្ថន័យសម្រាប់ទីផ្សារ',
    'social_read': '🧭 ការអានផ្នែកសេដ្ឋកិច្ច',
    'social_chain': '🔗 ខ្សែសង្វាក់ផលប៉ះពាល់',
    'social_calendar': '📅 កម្មវិធីកំណត់ ក្នុង 48 ម៉ោងខាងមុខ',
    'social_risks': '⚠️ អ្វីដែលអាចផ្លាស់ប្ដូរការវិភាគនេះ',
    'social_horizons': 'ក្នុង 4 ម៉ោង {now} · 1-5 ថ្ងៃ {week} · 2-8 សប្ដាហ៍ {months} · (+ = {meaning})',
    'social_evidence': '📡 អានពី {items} ព័ត៌មាន ពី {sources} ប្រភព ក្នុង 24 ម៉ោងចុងក្រោយ។',
    'social_track': '🎯 ការទាយពីមុនលើ {asset}៖ {hit} ក្នុង {total} ត្រូវបានបញ្ជាក់ដោយចលនាថ្លៃពិត ({days} ថ្ងៃ)។',
    'social_stamp': '🕒 {stamp} UTC',
    'social_breaking': '🚨 ព័ត៌មានបន្ទាន់',
    'social_alert': '⚠️ ការជូនដំណឹងទីផ្សារ',
    'social_update': '📰 បច្ចុប្បន្នភាពម៉ាក្រូ',
    'social_disclaimer': 'ℹ️ ការវិភាគផលប៉ះពាល់សេដ្ឋកិច្ច — មិនមែនការណែនាំពាណិជ្ជកម្ម គ្មានការកំណត់ទីតាំង '
                        'និងគ្មានគោលដៅថ្លៃទេ។',
    'telegram_test': '✅ Cambotix Economic Intelligence Engine ភ្ជាប់រួចរាល់។\n'
                     'ព្រឹត្តិបត្រម៉ាក្រូប្រចាំថ្ងៃមកដល់វេលា 06:00 UTC; ការជូនដំណឹងព្រឹត្តិការណ៍ឥទ្ធិពលខ្ពស់មកតាមពេលវិភាគរួច។',
    'machine_translated': 'អត្ថបទវិភាគបកប្រែដោយម៉ាស៊ីនពីភាសាអង់គ្លេស',
}

LABEL_TABLES = {'en': EN_LABELS, 'km': KM_LABELS}

# ---- macro-state vocabulary: positional, mirroring app.macro_state.LABELS (most positive score first) ------------
KM_STATES = {
    'inflation': ['លើសគោលដៅច្រើន', 'លើសគោលដៅ', 'ជិតគោលដៅ', 'ក្រោមគោលដៅ', 'ហានិភ័យធ្លាក់ថ្លៃ'],
    'employment': ['តឹងតែង', 'រឹងមាំ', 'មានតុល្យភាព', 'ចាប់ផ្ដើមទន់', 'ខ្សោយ'],
    'growth': ['ខ្លាំង', 'មធ្យម', 'យឺត', 'រួមតូច', 'វិបត្តិសេដ្ឋកិច្ច'],
    'monetary_policy': ['តឹងតែងខ្លាំង', 'តឹងតែង', 'អព្យាក្រឹត', 'បន្ធូរបន្ថយ', 'បន្ធូរបន្ថយខ្លាំង'],
    'liquidity': ['បរិបូរណ៍', 'គ្រប់គ្រាន់', 'អព្យាក្រឹត', 'តឹង', 'មានវិបត្តិ'],
    'fiscal': ['ពង្រីកខ្លាំង', 'ពង្រីក', 'អព្យាក្រឹត', 'រឹតបន្តឹង', 'សំចៃតឹងរឹង'],
    'risk_appetite': ['រំភើបខ្លាំង', 'ចូលចិត្តហានិភ័យ', 'អព្យាក្រឹត', 'គេចពីហានិភ័យ', 'ភ័យស្លន់ស្លោ'],
    'geopolitical_risk': ['ធ្ងន់ធ្ងរបំផុត', 'ខ្ពស់', 'ធម្មតា', 'ស្ងប់', 'ស្ងប់ខ្លាំង'],
    'energy': ['វិបត្តិផ្គត់ផ្គង់', 'តឹង', 'មានតុល្យភាព', 'បន្ធូរ', 'លើសផ្គត់ផ្គង់'],
    'regulation': ['គាំទ្រ', 'អំណោយផល', 'អព្យាក្រឹត', 'រឹតបន្តឹង', 'ប្រឆាំង'],
    'adoption': ['បង្កើនល្បឿន', 'កំពុងរីកចម្រើន', 'ស្ថិតស្ថេរ', 'យឺតចុះ', 'ថយចុះ'],
    'market_structure': ['រឹងមាំបំផុត', 'រឹងមាំ', 'អព្យាក្រឹត', 'ផុយស្រួយ', 'មានវិបត្តិ'],
}
STATE_TABLES = {'km': KM_STATES}

KM_DIMENSIONS = {
    'inflation': 'អតិផរណា', 'employment': 'ការងារ', 'growth': 'កំណើន', 'monetary_policy': 'នយោបាយរូបិយវត្ថុ',
    'liquidity': 'សន្ទនីយភាព', 'fiscal': 'នយោបាយសារពើពន្ធ', 'risk_appetite': 'ចំណង់ហានិភ័យ',
    'geopolitical_risk': 'ហានិភ័យភូមិសាស្ត្រនយោបាយ', 'energy': 'ថាមពល', 'regulation': 'បទប្បញ្ញត្តិ',
    'adoption': 'ការទទួលយក', 'market_structure': 'រចនាសម្ព័ន្ធទីផ្សារ',
}
DIMENSION_TABLES = {'km': KM_DIMENSIONS}

# The rest of the delivered vocabulary: trends, the analyst's reads, and the shared UNKNOWN.
# English is a delivery language too: the stored enums are for the trading engines that read the API, and a reader
# should not have to decode MORE_HAWKISH. Anything absent here is delivered as stored, so a new enum reads English
# rather than blank.
EN_ENUMS = {
    'UNKNOWN': 'not yet known',
    'RISING': 'rising', 'FALLING': 'falling', 'STABLE': 'steady',
    'IMPROVING': 'improving', 'DETERIORATING': 'deteriorating',
    'HOTTER': 'hotter', 'COOLER': 'cooler', 'NEUTRAL': 'neutral',
    'STRONGER': 'stronger', 'WEAKER': 'weaker', 'LOOSER': 'looser', 'TIGHTER': 'tighter',
    'MORE_HAWKISH': 'more hawkish', 'MORE_DOVISH': 'more dovish', 'NOT_RELEVANT': 'not relevant',
    'HIGHER': 'higher', 'LOWER': 'lower', 'UNCHANGED': 'unchanged',
    'RISK_ON': 'risk-on', 'RISK_OFF': 'risk-off',
    'CONFIRMS': 'confirms the current trend', 'CONTRADICTS': 'contradicts the current trend',
    'MIXED': 'mixed against the trend', 'NEW_THEME': 'a new theme',
    'IMMEDIATE': 'immediate', 'SHORT_TERM': 'short-term', 'MEDIUM_TERM': 'medium-term', 'LONG_TERM': 'long-term',
    'STRONG': 'strong', 'MODERATE': 'moderate', 'WEAK': 'weak',
    'HIGH': 'high', 'MEDIUM': 'medium', 'LOW': 'low',
}

KM_ENUMS = {
    'UNKNOWN': 'មិនទាន់ដឹង',
    'RISING': 'កើនឡើង', 'FALLING': 'ធ្លាក់ចុះ', 'STABLE': 'ស្ថិតស្ថេរ',
    'IMPROVING': 'ប្រសើរឡើង', 'DETERIORATING': 'ថយចុះ',
    'HIGH': 'ខ្ពស់', 'MEDIUM': 'មធ្យម', 'LOW': 'ទាប',
    'HOTTER': 'ក្ដៅជាង', 'COOLER': 'ត្រជាក់ជាង', 'NEUTRAL': 'អព្យាក្រឹត',
    'STRONGER': 'ខ្លាំងជាង', 'WEAKER': 'ខ្សោយជាង',
    'LOOSER': 'បន្ធូរជាង', 'TIGHTER': 'តឹងជាង',
    'MORE_HAWKISH': 'ទំនោរតឹងតែង', 'MORE_DOVISH': 'ទំនោរបន្ធូរ', 'NOT_RELEVANT': 'មិនពាក់ព័ន្ធ',
    'HIGHER': 'ខ្ពស់ជាង', 'LOWER': 'ទាបជាង', 'UNCHANGED': 'មិនប្រែប្រួល',
    'RISK_ON': 'ចូលចិត្តហានិភ័យ', 'RISK_OFF': 'គេចពីហានិភ័យ',
    'CONFIRMS': 'បញ្ជាក់និន្នាការ', 'CONTRADICTS': 'ផ្ទុយនិន្នាការ', 'MIXED': 'ចម្រុះ', 'NEW_THEME': 'ប្រធានបទថ្មី',
    'STRONG': 'ភស្តុតាងរឹងមាំ', 'MODERATE': 'ភស្តុតាងមធ្យម', 'WEAK': 'ភស្តុតាងខ្សោយ',
    'IMMEDIATE': 'ភ្លាមៗ', 'SHORT_TERM': 'រយៈពេលខ្លី', 'MEDIUM_TERM': 'រយៈពេលមធ្យម', 'LONG_TERM': 'រយៈពេលវែង',
    'BULLISH': 'ឡើង', 'BEARISH': 'ចុះ',
}
ENUM_TABLES = {'km': KM_ENUMS}

# The outlook's own words. Kept apart from ENUM_TABLES on purpose: BULLISH as a direction of travel reads "higher",
# but NEUTRAL as an inflation read is "neutral" and as a direction it is "no clear direction" - the same token means
# different things in the two places, so one table cannot serve both.
EN_OUTLOOK = {
    'BULLISH': 'expected higher', 'SLIGHT_BULLISH': 'leaning higher', 'NEUTRAL': 'no clear direction',
    'SLIGHT_BEARISH': 'leaning lower', 'BEARISH': 'expected lower',
    'BUILDING': 'and the pressure builds as the weeks pass',
    'FADING': 'strongest now and fading over the following weeks',
    'STEADY': 'holding across all three horizons',
    'FLIPPING': 'but the direction flips further out',
    'EVIDENCE_SOLID': 'solid', 'EVIDENCE_FAIR': 'fair', 'EVIDENCE_THIN': 'thin — treat this as a hint, not a read',
}
KM_OUTLOOK = {
    'BULLISH': 'ព្យាករឡើង', 'SLIGHT_BULLISH': 'ទំនោរឡើង', 'NEUTRAL': 'គ្មានទិសដៅច្បាស់',
    'SLIGHT_BEARISH': 'ទំនោរចុះ', 'BEARISH': 'ព្យាករចុះ',
    'BUILDING': 'ហើយសម្ពាធកើនឡើងតាមសប្ដាហ៍ដែលកន្លងទៅ',
    'FADING': 'ខ្លាំងបំផុតឥឡូវនេះ ហើយថយចុះក្នុងសប្ដាហ៍បន្ទាប់',
    'STEADY': 'ស្ថិតស្ថេរគ្រប់រយៈពេលទាំងបី',
    'FLIPPING': 'ប៉ុន្តែទិសដៅប្រែបញ្ច្រាសក្នុងរយៈពេលវែងជាង',
    'EVIDENCE_SOLID': 'រឹងមាំ', 'EVIDENCE_FAIR': 'មធ្យម', 'EVIDENCE_THIN': 'ស្តើង — ចាត់ទុកជាការណែនាំ មិនមែនការសម្រេច',
}
OUTLOOK_TABLES = {'en': EN_OUTLOOK, 'km': KM_OUTLOOK}

# The instruments and what "up" means for each. The English comes from app.outlook, which is also what the
# intelligence API serves; only the delivered wording is localized. Tickers and index names stay in Latin script.
KM_ASSETS = {'USD': 'ដុល្លារអាមេរិក', 'EURUSD': 'អឺរ៉ូ/ដុល្លារ', 'XAUUSD': 'មាស', 'OIL': 'ប្រេង',
             'US10Y': 'អត្រាចំណេញបណ្ណបំណុល ១០ឆ្នាំ អាមេរិក'}
KM_UP_MEANS = {
    'USD': 'ដុល្លារខ្លាំងឡើង', 'EURUSD': 'អឺរ៉ូខ្លាំងឡើងធៀបដុល្លារ', 'XAUUSD': 'ថ្លៃមាសឡើង',
    'BTC': 'ថ្លៃ BTC ឡើង', 'ETH': 'ថ្លៃ ETH ឡើង', 'SPX': 'សន្ទស្សន៍ឡើង', 'NASDAQ': 'សន្ទស្សន៍ឡើង',
    'US10Y': 'អត្រាចំណេញឡើង — ថ្លៃបណ្ណបំណុលចុះ', 'OIL': 'ថ្លៃប្រេងឡើង',
}
ASSET_TABLES = {'km': KM_ASSETS}
UP_MEANS_TABLES = {'km': KM_UP_MEANS}

# Direction of travel as a symbol - no language of its own, so both the brief and the posts read it from here.
TREND_ARROW = {'RISING': '↑', 'FALLING': '↓', 'STABLE': '→'}

KM_WEEKDAYS = {'Mon': 'ច័ន្ទ', 'Tue': 'អង្គារ', 'Wed': 'ពុធ', 'Thu': 'ព្រហ.', 'Fri': 'សុក្រ', 'Sat': 'សៅរ៍',
               'Sun': 'អាទិត្យ'}
WEEKDAY_TABLES = {'km': KM_WEEKDAYS}


def translation_active(lang: str) -> bool:
    """Whether prose in `lang` is actually being translated. A reader should be told which sentences passed through a
    translator, and which are the English the analyst wrote."""
    return lang != 'en' and anthropic_configured()


def labels(lang: str) -> dict:
    """The label table for `lang`, with English filling any key a translation has not caught up with."""
    if lang == 'en':
        return EN_LABELS
    return {**EN_LABELS, **LABEL_TABLES.get(lang, {})}


def dimension(name: str, lang: str) -> str:
    if lang == 'en':
        return name.replace('_', ' ')
    return DIMENSION_TABLES.get(lang, {}).get(name) or name.replace('_', ' ')


def enum(value: str, lang: str) -> str:
    """One stored enum value as delivered text. Unknown values pass through: a new enum reads English, never blank."""
    if not value:
        return value
    if lang == 'en':
        return EN_ENUMS.get(value, value)
    return ENUM_TABLES.get(lang, {}).get(value) or EN_ENUMS.get(value, value)


def asset_name(asset: str, lang: str) -> str:
    if lang == 'en':
        return EN_ASSETS.get(asset, asset)
    return ASSET_TABLES.get(lang, {}).get(asset) or EN_ASSETS.get(asset, asset)


def up_means(asset: str, lang: str) -> str:
    """What a positive score means for this asset, spelled out every time - US10Y is a yield, not a bond price."""
    if lang == 'en':
        return EN_UP_MEANS.get(asset, 'price higher')
    return UP_MEANS_TABLES.get(lang, {}).get(asset) or EN_UP_MEANS.get(asset, 'price higher')


def outlook_word(token: str, lang: str) -> str:
    """Direction of travel, shape of the path, or weight of evidence - see EN_OUTLOOK for why this is its own table."""
    if not token:
        return token
    return (OUTLOOK_TABLES.get(lang) or {}).get(token) or EN_OUTLOOK.get(token) or enum(token, lang)


def state_label(dim: str, value: str, lang: str) -> str:
    """A macro_state label, translated by its position in that dimension's vocabulary - TIGHT means one thing for
    employment and another for liquidity, so the same word must not serve both."""
    if lang == 'en':
        # The stored label is a machine token; a reader wants the words. Only the delivered text changes.
        return EN_ENUMS.get(value) or value.replace('_', ' ').lower()
    vocabulary, words = STATE_VOCABULARY.get(dim) or [], STATE_TABLES.get(lang, {}).get(dim) or []
    if value in vocabulary and len(words) == len(vocabulary):
        return words[vocabulary.index(value)]
    return enum(value, lang)


def state_reading(dim: str, state: str, trend: str, lang: str) -> str:
    """A dimension's state and where it is heading, as one phrase: "above target and rising".

    A steady state on a stable trend renders the same word twice ("adoption: steady and steady"), so the second
    clause is dropped - the arrow and the score carry the direction anyway.
    """
    state_words, trend_words = state_label(dim, state, lang), enum(trend, lang)
    if state_words == trend_words:
        return state_words
    return f'{state_words} and {trend_words}' if lang == 'en' else f'{state_words} និង{trend_words}'


def weekday(short_name: str, lang: str) -> str:
    if lang == 'en':
        return short_name
    return WEEKDAY_TABLES.get(lang, {}).get(short_name) or short_name


# ---- prose ------------------------------------------------------------------------------------------------------
_CACHE: dict[tuple[str, str], str] = {}
CACHE_LIMIT = 4000
_warned: set[str] = set()


def translate(texts: list[str], lang: str) -> list[str]:
    """Engine prose in `lang`, falling back to the English text per segment. Never raises: a brief that arrives in
    English is a smaller failure than a brief that does not arrive."""
    if lang == 'en' or not texts:
        return list(texts)
    if not anthropic_configured():
        if lang not in _warned:
            _warned.add(lang)
            log.info('prose stays English: %s needs an Anthropic key (TRANSLATE_MODEL); labels are translated anyway',
                     lang)
        return list(texts)
    pending = list(dict.fromkeys(text for text in texts if text and text.strip() and (lang, text) not in _CACHE))
    if pending:
        try:
            translated = ai.translate(pending, LANGUAGE_NAMES.get(lang, lang))
        except (ai.AIError, ValueError) as error:
            log.warning('translation to %s failed, delivering English: %s', lang, error)
            translated = None
        if translated and len(translated) != len(pending):
            # A shifted list would attach one segment's translation to another segment's text.
            log.warning('translation to %s returned %s of %s segments, delivering English', lang, len(translated),
                        len(pending))
        elif translated:
            if len(_CACHE) > CACHE_LIMIT:
                _CACHE.clear()
            _CACHE.update({(lang, source): out.strip() or source for source, out in zip(pending, translated)})
    return [_CACHE.get((lang, text), text) for text in texts]


def one(text: str | None, lang: str) -> str | None:
    return text if not text else translate([text], lang)[0]
