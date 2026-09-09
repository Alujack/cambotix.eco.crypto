"""Request bodies and the two LLM output contracts (extraction, analysis)."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import ASSET_UNIVERSE

Asset = Literal['USD', 'EURUSD', 'XAUUSD', 'BTC', 'ETH', 'SPX', 'NASDAQ', 'US10Y', 'OIL']
Category = Literal['INFLATION', 'EMPLOYMENT', 'GROWTH', 'MONETARY_POLICY', 'GLOBAL_GEOPOLITICAL', 'CRYPTO']
Region = Literal['US', 'EU', 'GLOBAL', 'CRYPTO']
Dimension = Literal['inflation', 'employment', 'growth', 'monetary_policy', 'liquidity', 'fiscal',
                    'risk_appetite', 'geopolitical_risk', 'energy', 'regulation', 'adoption', 'market_structure']
EventType = Literal[
    'CPI_RELEASE', 'PCE_RELEASE', 'PPI_RELEASE', 'INFLATION_EXPECTATIONS', 'WAGES_RELEASE',
    'NFP_RELEASE', 'JOBLESS_CLAIMS', 'JOLTS_RELEASE', 'ADP_RELEASE', 'LAYOFFS',
    'GDP_RELEASE', 'PMI_RELEASE', 'RETAIL_SALES_RELEASE', 'INDUSTRIAL_PRODUCTION', 'DURABLE_GOODS',
    'CONSUMER_CONFIDENCE', 'HOUSING_DATA',
    'FOMC_DECISION', 'FOMC_MINUTES', 'FED_SPEECH', 'FED_TESTIMONY', 'ECB_DECISION', 'ECB_SPEECH', 'BOE_DECISION',
    'BOJ_DECISION', 'PBOC_ACTION', 'BALANCE_SHEET_QT_QE', 'CENTRAL_BANK_OTHER',
    'WAR_CONFLICT', 'SANCTIONS', 'TARIFFS_TRADE', 'ELECTION', 'OIL_OPEC', 'SUPPLY_CHAIN',
    'GOVERNMENT_SHUTDOWN_DEBT', 'FISCAL_POLICY', 'BANKING_STRESS',
    'CRYPTO_REGULATION', 'SEC_ACTION', 'CFTC_ACTION', 'CRYPTO_ETF', 'ETF_FLOWS', 'EXCHANGE_INCIDENT',
    'EXCHANGE_LISTING', 'STABLECOIN', 'BITCOIN_MINING', 'PROTOCOL_UPGRADE', 'TOKEN_UNLOCK',
    'INSTITUTIONAL_ADOPTION', 'CORPORATE_TREASURY', 'CRYPTO_HACK', 'ONCHAIN_ACTIVITY',
    'MARKET_COMMENTARY', 'OTHER',
]
# Event types where the same type+country+date can still be several distinct events.
SUBJECT_EVENT_TYPES = {
    'FED_SPEECH', 'FED_TESTIMONY', 'ECB_SPEECH', 'CENTRAL_BANK_OTHER', 'WAR_CONFLICT', 'SANCTIONS', 'TARIFFS_TRADE',
    'ELECTION', 'SUPPLY_CHAIN', 'FISCAL_POLICY', 'BANKING_STRESS', 'CRYPTO_REGULATION', 'SEC_ACTION', 'CFTC_ACTION',
    'CRYPTO_ETF', 'EXCHANGE_INCIDENT', 'EXCHANGE_LISTING', 'STABLECOIN', 'BITCOIN_MINING', 'PROTOCOL_UPGRADE',
    'TOKEN_UNLOCK', 'INSTITUTIONAL_ADOPTION', 'CORPORATE_TREASURY', 'CRYPTO_HACK', 'ONCHAIN_ACTIVITY', 'LAYOFFS',
    'HOUSING_DATA', 'MARKET_COMMENTARY', 'OTHER',
}


# ---- ingestion -------------------------------------------------------------------------------------------------
class IngestArticle(BaseModel):
    model_config = ConfigDict(extra='ignore')
    headline: str = Field(min_length=3, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    externalId: str | None = Field(default=None, max_length=2000)
    publishedAt: str | datetime | None = None
    content: str | None = Field(default=None, max_length=20000)
    publisher: str | None = Field(default=None, max_length=200)
    topics: list[str] = Field(default_factory=list)
    source: str | None = None


class IngestArticlesRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    source: str | None = None
    items: list[IngestArticle] = Field(max_length=500)


class CalendarItem(BaseModel):
    model_config = ConfigDict(extra='ignore')
    title: str = Field(min_length=2, max_length=300)
    currency: str = Field(min_length=2, max_length=10)
    scheduledAt: str | datetime
    impact: str
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None


class IngestCalendarRequest(BaseModel):
    model_config = ConfigDict(extra='ignore')
    source: str = 'forexfactory_calendar'
    items: list[CalendarItem] = Field(max_length=1000)


# ---- stage 1: information extraction (cheap model) ------------------------------------------------------------
class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Extraction(StrictModel):
    is_relevant: bool = Field(description='False for content with no economic, policy, or crypto-market information.')
    event_type: EventType
    subject: str = Field(description='Short subject that distinguishes this event from others of the same type on the '
                                     'same day: speaker name, institution + action, company, or the metric name.')
    event_date: str = Field(description='YYYY-MM-DD (UTC) when the underlying event happened or was published.')
    countries: list[str] = Field(description='ISO-2 codes, or EU / GLOBAL. Most affected first.')
    categories: list[Category]
    institutions: list[str]
    people: list[str]
    assets: list[Asset] = Field(description='Assets whose economic drivers this information touches.')
    metric: str | None = Field(description='Name of the economic series if this reports a data print, else null.')
    actual: float | None = Field(description='Reported value as a plain number (3.1 for 3.1%), else null.')
    forecast: float | None
    previous: float | None
    unit: str | None = Field(description='%, % y/y, % m/m, thousands, index, bps, USD bn ... or null.')
    surprise: Literal['ABOVE_EXPECTATIONS', 'BELOW_EXPECTATIONS', 'IN_LINE', 'NOT_APPLICABLE']
    tone: Literal['HAWKISH', 'DOVISH', 'NEUTRAL', 'NOT_APPLICABLE'] = Field(
        description='For central-bank communication only; NOT_APPLICABLE otherwise.')
    is_market_reaction_coverage: bool = Field(
        description='True when the item mainly reports how markets moved in response to an event rather than the event.')
    importance: int = Field(ge=0, le=100, description='0 trivia ... 100 FOMC decision / CPI / NFP / major war escalation.')
    fact_summary: str = Field(description='One sentence, facts only, no interpretation.')


# ---- stage 2: economic analyst (reasoning model) --------------------------------------------------------------
Direction3 = Literal['BULLISH', 'BEARISH', 'NEUTRAL']


class HorizonImpact(StrictModel):
    direction: Direction3
    score: int = Field(ge=-100, le=100, description='Negative = bearish. Magnitude = expected strength x confidence.')


class AssetImpact(StrictModel):
    asset: Asset
    immediate: HorizonImpact = Field(description='0-4 hours.')
    short_term: HorizonImpact = Field(description='1-5 trading days.')
    medium_term: HorizonImpact = Field(description='2-8 weeks.')
    rationale: str

    @model_validator(mode='after')
    def _direction_follows_score(self):
        for impact in (self.immediate, self.short_term, self.medium_term):
            impact.direction = 'NEUTRAL' if abs(impact.score) <= 10 else ('BULLISH' if impact.score > 0 else 'BEARISH')
        return self


class EconomicInterpretation(StrictModel):
    inflation: Literal['HOTTER', 'COOLER', 'NEUTRAL']
    growth: Literal['STRONGER', 'WEAKER', 'NEUTRAL']
    employment: Literal['STRONGER', 'WEAKER', 'NEUTRAL']
    liquidity: Literal['LOOSER', 'TIGHTER', 'NEUTRAL']


class CentralBankImplication(StrictModel):
    fed: Literal['MORE_HAWKISH', 'MORE_DOVISH', 'NEUTRAL', 'NOT_RELEVANT']
    ecb: Literal['MORE_HAWKISH', 'MORE_DOVISH', 'NEUTRAL', 'NOT_RELEVANT']
    rate_cut_probability_impact: Literal['HIGHER', 'LOWER', 'UNCHANGED', 'NOT_RELEVANT']


class MacroStateUpdate(StrictModel):
    region: Region
    dimension: Dimension
    direction: Literal[-1, 0, 1] = Field(description='+1 pushes the dimension score up (hotter inflation, tighter '
                                                     'policy, more risk appetite, more supportive regulation ...).')
    magnitude: int = Field(ge=0, le=100, description='How hard this single event should move the living state.')
    reason: str


class Analysis(StrictModel):
    summary: str = Field(description='Two sentences a portfolio manager would read first.')
    what_happened: str
    why_it_matters: str
    what_changed_vs_expectations: str
    is_new_information: bool
    economic_interpretation: EconomicInterpretation
    central_bank_implication: CentralBankImplication
    risk_regime_impact: Literal['RISK_ON', 'RISK_OFF', 'NEUTRAL']
    causal_chain: list[str] = Field(description='3-7 steps from the event to the asset pressure.')
    relation_to_trend: Literal['CONFIRMS', 'CONTRADICTS', 'MIXED', 'NEW_THEME']
    horizon: Literal['IMMEDIATE', 'SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM']
    evidence_strength: Literal['STRONG', 'MODERATE', 'WEAK']
    confidence: int = Field(ge=0, le=100)
    asset_impacts: list[AssetImpact] = Field(description='One entry per asset in the universe that is affected.')
    macro_state_updates: list[MacroStateUpdate]
    key_risks: list[str]

    @model_validator(mode='after')
    def _one_impact_per_asset(self):
        seen: dict[str, AssetImpact] = {}
        for impact in self.asset_impacts:
            if impact.asset in ASSET_UNIVERSE and impact.asset not in seen:
                seen[impact.asset] = impact
        self.asset_impacts = list(seen.values())
        return self
