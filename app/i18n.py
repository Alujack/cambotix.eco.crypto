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

log = logging.getLogger('eco.i18n')
LANGUAGE_NAMES = {'en': 'English', 'km': 'Khmer (ភាសាខ្មែរ)'}

# ---- labels: the fixed furniture of a brief or an alert ----------------------------------------------------------
EN_LABELS = {
    'brief_title': '🌍 GLOBAL MACRO BRIEF',
    'pipeline_line': 'Pipeline: {received} items from {sources} sources in 24h · {queued} queued · '
                     '{analyzed} events analyzed · analyst {model}',
    'flagged': '⚠ {flagged}/{total} analyses have self-consistency flags (GET /analysis-quality) — treat their '
               'scores with caution.',
    'ai_off': 'AI analysis is OFF — configure AI_PROVIDER (ollama / anthropic) to populate regime, developments and '
              'asset pressure.',
    'macro_regime': 'MACRO REGIME',
    'unknown_dims': '⚪ {n} dimension(s) not yet informed by any analyzed event',
    'no_macro_state': '⚪ No macro state yet — the first analyzed events populate this section.',
    'risk_liquidity': 'Risk regime: {risk}   Global liquidity: {liquidity}',
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
    'asset_pressure': 'ASSET MACRO PRESSURE',
    'all_neutral': '(no analyzed events yet — all assets neutral)',
    'key_risks': 'KEY RISKS',
    'none_recorded': '(none recorded)',
    'importance': 'importance',
    'read_inflation': 'Inflation',
    'read_growth': 'Growth',
    'read_liquidity': 'Liquidity',
    'read_risk': 'Risk',
    'impact_now': 'Impact now/1-5d',
    'chain': 'Chain',
    'confidence': 'confidence',
    'trend_relation': '{relation} trend',
    'evidence': '{evidence} evidence',
    'source_items': '{n} source item(s)',
    'event_tail': 'event {id} · {stamp} UTC',
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
    'risk_liquidity': 'របបហានិភ័យ៖ {risk}   សន្ទនីយភាពសកល៖ {liquidity}',
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
    'asset_pressure': 'សម្ពាធម៉ាក្រូលើទ្រព្យ',
    'all_neutral': '(មិនទាន់មានព្រឹត្តិការណ៍វិភាគ — ទ្រព្យទាំងអស់អព្យាក្រឹត)',
    'key_risks': 'ហានិភ័យសំខាន់ៗ',
    'none_recorded': '(មិនមានកំណត់ត្រា)',
    'importance': 'សំខាន់',
    'read_inflation': 'អតិផរណា',
    'read_growth': 'កំណើន',
    'read_liquidity': 'សន្ទនីយភាព',
    'read_risk': 'ហានិភ័យ',
    'impact_now': 'ផលប៉ះពាល់ ឥឡូវ/1-5 ថ្ងៃ',
    'chain': 'ខ្សែសង្វាក់',
    'confidence': 'ទំនុកចិត្ត',
    'trend_relation': '{relation}',
    'evidence': '{evidence}',
    'source_items': 'ព័ត៌មានប្រភព {n}',
    'event_tail': 'ព្រឹត្តិការណ៍ {id} · {stamp} UTC',
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
    if lang == 'en' or not value:
        return value
    return ENUM_TABLES.get(lang, {}).get(value) or value


def state_label(dim: str, value: str, lang: str) -> str:
    """A macro_state label, translated by its position in that dimension's vocabulary - TIGHT means one thing for
    employment and another for liquidity, so the same word must not serve both."""
    if lang == 'en':
        return value
    vocabulary, words = STATE_VOCABULARY.get(dim) or [], STATE_TABLES.get(lang, {}).get(dim) or []
    if value in vocabulary and len(words) == len(vocabulary):
        return words[vocabulary.index(value)]
    return enum(value, lang)


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
