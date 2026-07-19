-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Raw ads from Meta Ad Library
CREATE TABLE IF NOT EXISTS ads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_id TEXT UNIQUE NOT NULL,
    platform TEXT NOT NULL DEFAULT 'meta',
    industry TEXT,
    spend_range TEXT,
    impression_range TEXT,
    days_running INTEGER,
    raw_copy TEXT,
    snapshot_url TEXT,
    delivery_start_date DATE,
    delivery_end_date DATE,
    page_name TEXT,
    region TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- AI-processed ad records
CREATE TABLE IF NOT EXISTS processed_ads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_id TEXT REFERENCES ads(ad_id) ON DELETE CASCADE,
    hook TEXT,
    hook_type TEXT,
    cta TEXT,
    cta_type TEXT,
    framework TEXT,
    tone TEXT,
    format TEXT,
    visual_description TEXT,
    copy_length TEXT,
    key_emotion TEXT,
    embedding vector(1536),
    model_used TEXT,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for vector similarity search
CREATE INDEX IF NOT EXISTS idx_processed_ads_embedding 
ON processed_ads USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- Additional indexes for common queries
CREATE INDEX IF NOT EXISTS idx_ads_platform ON ads(platform);
CREATE INDEX IF NOT EXISTS idx_ads_industry ON ads(industry);
CREATE INDEX IF NOT EXISTS idx_processed_ads_framework ON processed_ads(framework);
CREATE INDEX IF NOT EXISTS idx_processed_ads_tone ON processed_ads(tone);

-- Saved copy variants (user-generated or AI-generated and saved)
CREATE TABLE IF NOT EXISTS copy_variants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_session TEXT,
    platform TEXT NOT NULL,
    industry TEXT,
    framework TEXT,
    headline TEXT,
    hook TEXT,
    body_copy TEXT,
    cta TEXT,
    model_used TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_copy_variants_platform ON copy_variants(platform);
CREATE INDEX IF NOT EXISTS idx_copy_variants_framework ON copy_variants(framework);
