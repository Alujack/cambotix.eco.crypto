-- Economic Intelligence Engine schema. Applied once by the Postgres entrypoint (and by scripts/test.sh to eco_tests).
CREATE EXTENSION IF NOT EXISTS vector;

-- Source registry. Seeded from sources/registry.json on engine start; edit the JSON, not the rows.
CREATE TABLE IF NOT EXISTS sources (
    key text PRIMARY KEY,
    name text NOT NULL,
    category text NOT NULL CHECK (category IN ('central_bank', 'official_statistics', 'regulator', 'financial_media',
                                               'crypto_media', 'calendar', 'market_data')),
    country text NOT NULL DEFAULT 'GLOBAL',
    type text NOT NULL CHECK (type IN ('rss', 'api', 'webhook', 'synthetic')),
    url text,
    priority integer NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),
    reliability integer NOT NULL DEFAULT 50 CHECK (reliability BETWEEN 0 AND 100),
    enabled boolean NOT NULL DEFAULT true,
    default_categories text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Economic events: one row per real-world event, no matter how many outlets cover it.
CREATE TABLE IF NOT EXISTS economic_events (
    id text PRIMARY KEY,
    event_key text NOT NULL UNIQUE,
    event_type text NOT NULL,
    title text NOT NULL,
    subject text,
    event_date date,
    countries text[] NOT NULL DEFAULT '{}',
    categories text[] NOT NULL DEFAULT '{}',
    importance integer NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'analyzing', 'analyzed', 'error')),
    primary_source_key text REFERENCES sources(key),
    primary_article_id text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    article_count integer NOT NULL DEFAULT 0,
    facts jsonb NOT NULL DEFAULT '{}',
    affected_assets text[] NOT NULL DEFAULT '{}',
    needs_analysis boolean NOT NULL DEFAULT true,
    analysis_version integer NOT NULL DEFAULT 0,
    analyzed_at timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    error_code text
);
CREATE INDEX IF NOT EXISTS economic_events_analysis_queue_idx ON economic_events (next_attempt_at, importance DESC)
    WHERE needs_analysis;
CREATE INDEX IF NOT EXISTS economic_events_recent_idx ON economic_events (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS economic_events_type_date_idx ON economic_events (event_type, event_date DESC);

-- Every collected item, normalized to one shape regardless of source. content_hash is the duplicate gate.
CREATE TABLE IF NOT EXISTS raw_articles (
    id text PRIMARY KEY,
    content_hash text NOT NULL UNIQUE,
    source_key text NOT NULL REFERENCES sources(key),
    publisher text,
    reliability integer NOT NULL,
    published_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    headline text NOT NULL,
    content text,
    url text,
    countries text[] NOT NULL DEFAULT '{}',
    categories text[] NOT NULL DEFAULT '{}',
    entities text[] NOT NULL DEFAULT '{}',
    assets text[] NOT NULL DEFAULT '{}',
    importance_prior integer NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'extracting', 'extracted', 'ignored', 'error')),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    extraction jsonb,
    event_id text REFERENCES economic_events(id),
    error_code text
);
CREATE INDEX IF NOT EXISTS raw_articles_queue_idx ON raw_articles (next_attempt_at, received_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS raw_articles_published_idx ON raw_articles (published_at DESC);
CREATE INDEX IF NOT EXISTS raw_articles_event_idx ON raw_articles (event_id);

CREATE TABLE IF NOT EXISTS event_articles (
    event_id text NOT NULL REFERENCES economic_events(id),
    article_id text NOT NULL REFERENCES raw_articles(id),
    role text NOT NULL CHECK (role IN ('primary', 'supporting', 'reaction')),
    linked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, article_id)
);

-- Stage-2 output. Versioned: an event is re-analyzed when materially new coverage arrives.
CREATE TABLE IF NOT EXISTS event_analysis (
    id bigserial PRIMARY KEY,
    event_id text NOT NULL REFERENCES economic_events(id),
    version integer NOT NULL,
    model text NOT NULL,
    summary text NOT NULL,
    what_happened text,
    why_it_matters text,
    what_changed text,
    economic_interpretation jsonb NOT NULL,
    central_bank_implication jsonb NOT NULL,
    risk_regime_impact text,
    causal_chain jsonb NOT NULL DEFAULT '[]',
    relation_to_trend text,
    horizon text,
    is_new_information boolean,
    evidence_strength text,
    confidence integer NOT NULL,
    key_risks jsonb NOT NULL DEFAULT '[]',
    consistency_flags jsonb NOT NULL DEFAULT '[]',
    raw jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_id, version)
);

-- Per-asset, per-horizon impact. Scores are -100..100 (negative = bearish); direction is the sign as a label.
CREATE TABLE IF NOT EXISTS asset_impacts (
    id bigserial PRIMARY KEY,
    analysis_id bigint NOT NULL REFERENCES event_analysis(id),
    event_id text NOT NULL REFERENCES economic_events(id),
    asset text NOT NULL,
    immediate_direction text NOT NULL,
    immediate_score integer NOT NULL,
    short_term_direction text NOT NULL,
    short_term_score integer NOT NULL,
    medium_term_direction text NOT NULL,
    medium_term_score integer NOT NULL,
    rationale text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (analysis_id, asset)
);
CREATE INDEX IF NOT EXISTS asset_impacts_asset_idx ON asset_impacts (asset, created_at DESC);

-- Scheduled releases (calendar). actual fills in after the print; a HIGH print spawns a synthetic release article.
CREATE TABLE IF NOT EXISTS economic_releases (
    id text PRIMARY KEY,
    source_key text NOT NULL REFERENCES sources(key),
    currency text NOT NULL,
    title text NOT NULL,
    impact text NOT NULL CHECK (impact IN ('LOW', 'MEDIUM', 'HIGH')),
    scheduled_at timestamptz NOT NULL,
    actual text,
    forecast text,
    previous text,
    event_id text REFERENCES economic_events(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (currency, title, scheduled_at)
);
CREATE INDEX IF NOT EXISTS economic_releases_schedule_idx ON economic_releases (scheduled_at);

-- What markets actually did after an event, per window, against what the analyst expected.
CREATE TABLE IF NOT EXISTS market_reactions (
    id bigserial PRIMARY KEY,
    event_id text NOT NULL REFERENCES economic_events(id),
    asset text NOT NULL,
    anchor_at timestamptz NOT NULL,
    anchor_price numeric NOT NULL,
    window_label text NOT NULL CHECK (window_label IN ('5m', '15m', '1h', '4h', '24h')),
    due_at timestamptz NOT NULL,
    price numeric,
    change_pct numeric,
    measured_at timestamptz,
    expected_direction text,
    interpretation text CHECK (interpretation IN ('CONFIRMED', 'REJECTED', 'FLAT')),
    UNIQUE (event_id, asset, window_label)
);
CREATE INDEX IF NOT EXISTS market_reactions_due_idx ON market_reactions (due_at) WHERE measured_at IS NULL;

-- The living picture of the economy. One row per region x dimension; every change is journaled.
CREATE TABLE IF NOT EXISTS macro_state (
    region text NOT NULL,
    dimension text NOT NULL,
    score integer NOT NULL DEFAULT 0 CHECK (score BETWEEN -100 AND 100),
    state text NOT NULL DEFAULT 'UNKNOWN',
    trend text NOT NULL DEFAULT 'STABLE',
    confidence integer NOT NULL DEFAULT 0,
    last_event_id text REFERENCES economic_events(id),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (region, dimension)
);
CREATE TABLE IF NOT EXISTS macro_state_history (
    id bigserial PRIMARY KEY,
    region text NOT NULL,
    dimension text NOT NULL,
    prev_score integer NOT NULL,
    new_score integer NOT NULL,
    prev_state text NOT NULL,
    new_state text NOT NULL,
    prev_trend text NOT NULL,
    new_trend text NOT NULL,
    event_id text REFERENCES economic_events(id),
    weight numeric NOT NULL,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS macro_state_history_dim_idx ON macro_state_history (region, dimension, created_at DESC);

-- Economic priors: the analyst starts from these and adjusts with context. Not trading rules.
CREATE TABLE IF NOT EXISTS economic_asset_map (
    driver text NOT NULL,
    asset text NOT NULL,
    direction integer NOT NULL CHECK (direction BETWEEN -2 AND 2),
    note text,
    PRIMARY KEY (driver, asset)
);

CREATE TABLE IF NOT EXISTS briefs (
    id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('daily', 'weekly')),
    brief_date date NOT NULL,
    macro_regime jsonb NOT NULL,
    asset_pressure jsonb NOT NULL,
    developments jsonb NOT NULL,
    upcoming jsonb NOT NULL,
    key_risks jsonb NOT NULL,
    narrative text,
    text text NOT NULL,
    model text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, brief_date)
);

ALTER TABLE event_analysis ADD COLUMN IF NOT EXISTS consistency_flags jsonb NOT NULL DEFAULT '[]';

-- Notification outbox (Telegram). Enqueued by the engine, flushed immediately and retried by n8n workflow 12.
CREATE TABLE IF NOT EXISTS notifications (
    id bigserial PRIMARY KEY,
    channel text NOT NULL DEFAULT 'telegram',
    kind text NOT NULL CHECK (kind IN ('brief', 'event_alert', 'test', 'social_brief', 'social_event')),
    ref_id text NOT NULL,
    text text NOT NULL,
    parse_mode text,
    -- Destination chat or channel; NULL means the operator's TELEGRAM_CHAT_ID. Social posts carry the channel.
    target text,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'failed')),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    UNIQUE (kind, ref_id)
);
CREATE INDEX IF NOT EXISTS notifications_pending_idx ON notifications (next_attempt_at) WHERE status = 'pending';

-- Databases created before the public-channel posts (app.social) predate the target column and the two social kinds.
ALTER TABLE notifications ADD COLUMN IF NOT EXISTS target text;
ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check
    CHECK (kind IN ('brief', 'event_alert', 'test', 'social_brief', 'social_event'));

-- pgvector memory. Dimension is left open so the embedding provider can change; always filter by model.
CREATE TABLE IF NOT EXISTS knowledge_embeddings (
    id bigserial PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('article', 'event', 'analysis', 'brief')),
    ref_id text NOT NULL,
    model text NOT NULL,
    embedding vector NOT NULL,
    text text NOT NULL,
    event_id text REFERENCES economic_events(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, ref_id, model)
);
CREATE INDEX IF NOT EXISTS knowledge_embeddings_kind_idx ON knowledge_embeddings (kind, model, created_at DESC);

INSERT INTO macro_state (region, dimension) VALUES
    ('US', 'inflation'), ('US', 'employment'), ('US', 'growth'), ('US', 'monetary_policy'), ('US', 'liquidity'),
    ('US', 'fiscal'),
    ('EU', 'inflation'), ('EU', 'growth'), ('EU', 'monetary_policy'),
    ('GLOBAL', 'risk_appetite'), ('GLOBAL', 'geopolitical_risk'), ('GLOBAL', 'liquidity'), ('GLOBAL', 'energy'),
    ('CRYPTO', 'regulation'), ('CRYPTO', 'adoption'), ('CRYPTO', 'market_structure')
ON CONFLICT DO NOTHING;

INSERT INTO economic_asset_map (driver, asset, direction, note) VALUES
    ('HIGHER_INFLATION', 'USD', 1, 'Tighter expected policy'), ('HIGHER_INFLATION', 'XAUUSD', -1, 'Higher real-rate expectations'),
    ('HIGHER_INFLATION', 'BTC', -1, 'Tighter liquidity'), ('HIGHER_INFLATION', 'SPX', -1, NULL), ('HIGHER_INFLATION', 'US10Y', 1, 'Yields up'),
    ('RATE_CUTS', 'USD', -1, NULL), ('RATE_CUTS', 'XAUUSD', 1, NULL), ('RATE_CUTS', 'BTC', 1, NULL), ('RATE_CUTS', 'SPX', 1, NULL),
    ('RATE_CUTS', 'US10Y', -1, 'Yields down'),
    ('QE', 'USD', -1, NULL), ('QE', 'XAUUSD', 1, NULL), ('QE', 'BTC', 1, NULL), ('QE', 'SPX', 1, NULL), ('QE', 'US10Y', -1, NULL),
    ('STRONG_JOBS', 'USD', 1, NULL), ('STRONG_JOBS', 'XAUUSD', -1, NULL), ('STRONG_JOBS', 'BTC', 0, 'Growth vs liquidity offset'),
    ('STRONG_JOBS', 'SPX', 1, NULL), ('STRONG_JOBS', 'US10Y', 1, NULL),
    ('RECESSION_RISK', 'USD', -1, NULL), ('RECESSION_RISK', 'XAUUSD', 1, NULL), ('RECESSION_RISK', 'BTC', -1, NULL),
    ('RECESSION_RISK', 'SPX', -1, NULL), ('RECESSION_RISK', 'US10Y', -1, 'Flight to bonds'),
    ('MAJOR_WAR', 'USD', 0, 'Haven bid vs growth hit'), ('MAJOR_WAR', 'XAUUSD', 1, NULL), ('MAJOR_WAR', 'BTC', -1, NULL),
    ('MAJOR_WAR', 'SPX', -1, NULL), ('MAJOR_WAR', 'US10Y', -1, NULL), ('MAJOR_WAR', 'OIL', 1, NULL),
    ('TARIFFS_TRADE_WAR', 'USD', 1, 'Stagflationary: import prices up, growth down'),
    ('TARIFFS_TRADE_WAR', 'XAUUSD', 1, 'Haven and inflation hedge'), ('TARIFFS_TRADE_WAR', 'BTC', -1, 'Risk-off'),
    ('TARIFFS_TRADE_WAR', 'SPX', -1, 'Margin and demand hit'),
    ('TARIFFS_TRADE_WAR', 'US10Y', 0, 'Inflation lifts yields, growth fear lowers them'),
    ('SUPPLY_SHOCK', 'OIL', 1, NULL), ('SUPPLY_SHOCK', 'XAUUSD', 1, NULL), ('SUPPLY_SHOCK', 'SPX', -1, NULL),
    ('SUPPLY_SHOCK', 'US10Y', 1, 'Inflation expectations up'),
    ('BANKING_STRESS', 'USD', -1, NULL), ('BANKING_STRESS', 'XAUUSD', 1, NULL), ('BANKING_STRESS', 'SPX', -1, NULL),
    ('BANKING_STRESS', 'US10Y', -1, 'Flight to quality'), ('BANKING_STRESS', 'BTC', -1, 'Liquidity stress dominates'),
    ('CRYPTO_ETF_APPROVAL', 'BTC', 2, NULL), ('CRYPTO_ETF_APPROVAL', 'ETH', 2, NULL),
    ('CRYPTO_REGULATORY_CRACKDOWN', 'BTC', -2, NULL), ('CRYPTO_REGULATORY_CRACKDOWN', 'ETH', -2, NULL),
    ('EXCHANGE_FAILURE', 'BTC', -2, NULL), ('EXCHANGE_FAILURE', 'ETH', -2, NULL),
    ('STABLECOIN_GROWTH', 'BTC', 1, 'Crypto liquidity'), ('STABLECOIN_GROWTH', 'ETH', 1, NULL)
ON CONFLICT DO NOTHING;
