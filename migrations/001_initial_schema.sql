-- Enums
CREATE TYPE user_role AS ENUM ('RENTER', 'ADMIN');
CREATE TYPE search_status AS ENUM ('DRAFT', 'ACTIVE', 'PAUSED', 'CLOSED');
CREATE TYPE listing_type AS ENUM ('ENTIRE_PROPERTY', 'PRIVATE_ROOM', 'SHARED_ROOM');
CREATE TYPE content_type AS ENUM ('PROPERTY_LISTING', 'RENTER_REQUIREMENT', 'OTHER', 'UNKNOWN');
CREATE TYPE availability_status AS ENUM ('UNKNOWN', 'AVAILABLE', 'UNAVAILABLE', 'STALE');
CREATE TYPE preference_importance AS ENUM ('REQUIRED', 'PREFERRED', 'DOES_NOT_MATTER');
CREATE TYPE match_status AS ENUM ('REJECTED', 'POSSIBLE_MATCH', 'STRONG_MATCH', 'NEEDS_QUALIFICATION', 'QUALIFIED', 'SKIPPED');
CREATE TYPE conversation_status AS ENUM ('APPROVED_FOR_CONTACT', 'CONTACTED', 'AWAITING_REPLY', 'QUALIFYING', 'ESCALATED_TO_RENTER', 'QUALIFIED', 'READY_FOR_SCHEDULING', 'NO_RESPONSE', 'CLOSED');
CREATE TYPE visit_status AS ENUM ('PROPOSED', 'AWAITING_RENTER_CONFIRMATION', 'CONFIRMED', 'CANCELLED', 'COMPLETED');
CREATE TYPE contact_channel_type AS ENUM ('WHATSAPP', 'EMAIL', 'PHONE', 'TELEGRAM');
CREATE TYPE ingestion_status AS ENUM ('COLLECTING_INFO', 'EXTRACTING', 'NEEDS_REVIEW', 'COLLECTING_MEDIA', 'READY_FOR_APPROVAL', 'APPROVED', 'REJECTED', 'FAILED');

-- Tables
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id BIGINT UNIQUE NOT NULL,
    telegram_username TEXT,
    display_name TEXT,
    role user_role NOT NULL DEFAULT 'RENTER',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE search_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    status search_status NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    city TEXT NOT NULL DEFAULT 'Hyderabad',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    last_activated_at TIMESTAMPTZ,
    paused_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ
);

CREATE TABLE search_requirements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID UNIQUE REFERENCES search_sessions(id),
    listing_types listing_type[] NOT NULL,
    preferred_locations TEXT[] NOT NULL,
    acceptable_locations TEXT[] NOT NULL DEFAULT '{}',
    excluded_locations TEXT[] NOT NULL DEFAULT '{}',
    work_location TEXT,
    target_rent INTEGER NOT NULL,
    max_rent INTEGER NOT NULL,
    preferred_move_in_date DATE,
    latest_move_in_date DATE,
    preferred_property_configurations TEXT[],
    core_preferences JSONB NOT NULL DEFAULT '{}',
    additional_preferences JSONB NOT NULL DEFAULT '{}',
    scoring_weights JSONB NOT NULL DEFAULT '{}',
    raw_requirement_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE renter_availability (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    search_id UUID REFERENCES search_sessions(id),
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    general_windows JSONB NOT NULL,
    one_off_overrides JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id UUID REFERENCES users(id),
    mode TEXT NOT NULL,
    status ingestion_status NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE ingestion_inputs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_session_id UUID REFERENCES ingestion_sessions(id),
    group_key TEXT,
    input_type TEXT NOT NULL,
    telegram_file_id TEXT,
    telegram_file_unique_id TEXT,
    text_content TEXT,
    caption TEXT,
    is_information_bearing BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE listing_drafts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ingestion_session_id UUID REFERENCES ingestion_sessions(id),
    group_key TEXT,
    content_type content_type NOT NULL,
    canonical_payload JSONB NOT NULL,
    extracted_context JSONB NOT NULL DEFAULT '{}',
    conflicts JSONB NOT NULL DEFAULT '[]',
    extraction_status TEXT NOT NULL,
    model_metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_type listing_type NOT NULL,
    city TEXT NOT NULL,
    locality TEXT NOT NULL,
    location_text TEXT,
    landmark TEXT,
    property_configuration TEXT,
    room_occupancy TEXT,
    rent INTEGER NOT NULL,
    maintenance INTEGER,
    maintenance_mandatory BOOLEAN,
    deposit INTEGER,
    brokerage INTEGER,
    currency TEXT NOT NULL DEFAULT 'INR',
    available_from DATE,
    availability_status availability_status NOT NULL DEFAULT 'UNKNOWN',
    last_verified_at TIMESTAMPTZ,
    furnishing TEXT,
    attached_bathroom BOOLEAN,
    car_parking BOOLEAN,
    bike_parking BOOLEAN,
    balcony BOOLEAN,
    pets_allowed BOOLEAN,
    power_backup BOOLEAN,
    gated_community BOOLEAN,
    extracted_context JSONB NOT NULL DEFAULT '{}',
    source_summary TEXT,
    created_from_draft_id UUID REFERENCES listing_drafts(id),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE listing_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID REFERENCES listings(id),
    source_type TEXT NOT NULL,
    telegram_file_id TEXT,
    telegram_file_unique_id TEXT,
    raw_text TEXT,
    source_url TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE listing_media (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID REFERENCES listings(id),
    telegram_file_id TEXT NOT NULL,
    telegram_file_unique_id TEXT,
    media_type TEXT NOT NULL DEFAULT 'PHOTO',
    sort_order INTEGER NOT NULL DEFAULT 0,
    caption TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID REFERENCES listings(id),
    name TEXT,
    role TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contact_channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contact_id UUID REFERENCES contacts(id),
    type contact_channel_type NOT NULL,
    value TEXT NOT NULL,
    explicit BOOLEAN NOT NULL DEFAULT FALSE,
    is_usable BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE matches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID REFERENCES search_sessions(id),
    listing_id UUID REFERENCES listings(id),
    status match_status NOT NULL,
    fit_score NUMERIC(5,2),
    information_completeness NUMERIC(5,2),
    hard_rejection_reasons JSONB NOT NULL DEFAULT '[]',
    positive_reasons JSONB NOT NULL DEFAULT '[]',
    missing_information JSONB NOT NULL DEFAULT '[]',
    soft_context_evaluation JSONB NOT NULL DEFAULT '{}',
    score_breakdown JSONB NOT NULL DEFAULT '{}',
    search_version INTEGER NOT NULL DEFAULT 1,
    listing_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(search_id, listing_id)
);

CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID REFERENCES search_sessions(id),
    listing_id UUID REFERENCES listings(id),
    contact_id UUID REFERENCES contacts(id),
    status conversation_status NOT NULL,
    active_channel_id UUID REFERENCES contact_channels(id),
    outreach_approved_at TIMESTAMPTZ,
    last_message_at TIMESTAMPTZ,
    follow_up_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    channel_type contact_channel_type NOT NULL,
    direction TEXT NOT NULL,
    external_message_id TEXT,
    text TEXT NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}',
    parsed_facts JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE outreach_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id),
    contact_channel_id UUID REFERENCES contact_channels(id),
    attempt_type TEXT NOT NULL,
    status TEXT NOT NULL,
    sent_at TIMESTAMPTZ,
    failure_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE visits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    search_id UUID REFERENCES search_sessions(id),
    listing_id UUID REFERENCES listings(id),
    contact_id UUID REFERENCES contacts(id),
    status visit_status NOT NULL,
    proposed_start TIMESTAMPTZ,
    confirmed_start TIMESTAMPTZ,
    location_text TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ
);

CREATE TABLE agent_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    run_after TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task TEXT NOT NULL,
    input_reference JSONB NOT NULL DEFAULT '{}',
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    success BOOLEAN NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX idx_users_telegram_user_id ON users(telegram_user_id);
CREATE INDEX idx_search_sessions_user_id_status ON search_sessions(user_id, status);
CREATE INDEX idx_listings_search ON listings(city, locality, listing_type, availability_status);
CREATE INDEX idx_listings_rent ON listings(rent);
CREATE INDEX idx_matches_search_id_status ON matches(search_id, status);
CREATE INDEX idx_matches_listing_id ON matches(listing_id);
CREATE INDEX idx_conversations_status_last_message_at ON conversations(status, last_message_at);
CREATE INDEX idx_agent_jobs_status_run_after ON agent_jobs(status, run_after);
CREATE INDEX idx_agent_jobs_idempotency_key ON agent_jobs(idempotency_key);

-- Atomically claims one due job. SKIP LOCKED prevents concurrent workers from
-- processing the same job after retries or a process restart.
CREATE OR REPLACE FUNCTION claim_next_agent_job(p_worker_id TEXT)
RETURNS SETOF agent_jobs
LANGUAGE plpgsql
AS $$
DECLARE
    claimed_job agent_jobs%ROWTYPE;
BEGIN
    SELECT * INTO claimed_job
    FROM agent_jobs
    WHERE status = 'PENDING' AND run_after <= now()
    ORDER BY run_after, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    UPDATE agent_jobs
    SET status = 'RUNNING',
        locked_at = now(),
        locked_by = p_worker_id,
        attempts = claimed_job.attempts + 1,
        updated_at = now()
    WHERE id = claimed_job.id
    RETURNING * INTO claimed_job;

    RETURN NEXT claimed_job;
END;
$$;
