-- Atomic renter-search persistence and activation.
--
-- Run this migration manually in the Supabase SQL editor before deploying the
-- application code that calls these RPCs. It intentionally performs no data
-- cleanup: duplicate open searches must be reviewed by an operator first.

BEGIN;

ALTER TABLE public.search_sessions
    ADD COLUMN IF NOT EXISTS creation_key UUID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_search_sessions_creation_key
    ON public.search_sessions (creation_key)
    WHERE creation_key IS NOT NULL;

-- FlatHunter V0 supports one open (ACTIVE or PAUSED) search per renter. Abort
-- with a stable message instead of silently choosing or closing existing rows.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.search_sessions
        WHERE user_id IS NOT NULL
          AND status IN ('ACTIVE', 'PAUSED')
        GROUP BY user_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'OPEN_SEARCH_PRECHECK_FAILED';
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_search_sessions_one_open_per_user
    ON public.search_sessions (user_id)
    WHERE user_id IS NOT NULL
      AND status IN ('ACTIVE', 'PAUSED');

-- CREATE INDEX IF NOT EXISTS checks only the relation name. A pre-existing
-- same-name index can therefore be accepted even when it protects the wrong
-- table, key, or row set. Verify the complete safety contract before creating
-- any lifecycle RPCs. Predicate text is canonicalized only for harmless
-- deparser differences (case, quoting, whitespace, parentheses, and an
-- optional public schema qualifier); equality then rejects every extra clause.
DO $$
DECLARE
    v_creation_index RECORD;
    v_creation_index_found BOOLEAN;
    v_open_index RECORD;
    v_open_index_found BOOLEAN;
BEGIN
    SELECT
        index_state.indexrelid AS index_oid,
        index_state.indrelid AS table_oid,
        index_state.indisunique AS is_unique,
        index_state.indisvalid AS is_valid,
        index_state.indisready AS is_ready,
        index_state.indisprimary AS is_primary,
        index_state.indisexclusion AS is_exclusion,
        index_state.indnkeyatts AS key_count,
        index_state.indnatts AS total_attribute_count,
        index_state.indexprs IS NULL AS has_no_expressions,
        access_method.amname AS access_method,
        ARRAY(
            SELECT attribute.attname::TEXT
            FROM unnest(index_state.indkey::SMALLINT[])
                WITH ORDINALITY AS key_column(attnum, position)
            JOIN pg_catalog.pg_attribute AS attribute
              ON attribute.attrelid = index_state.indrelid
             AND attribute.attnum = key_column.attnum
            WHERE key_column.position <= index_state.indnkeyatts
            ORDER BY key_column.position
        ) AS key_columns,
        replace(
            regexp_replace(
                lower(
                    pg_catalog.pg_get_expr(
                        index_state.indpred,
                        index_state.indrelid,
                        FALSE
                    )
                ),
                '[[:space:]()"]+',
                '',
                'g'
            ),
            'public.',
            ''
        ) AS canonical_predicate
    INTO v_creation_index
    FROM pg_catalog.pg_index AS index_state
    JOIN pg_catalog.pg_class AS index_class
      ON index_class.oid = index_state.indexrelid
    JOIN pg_catalog.pg_namespace AS index_schema
      ON index_schema.oid = index_class.relnamespace
    JOIN pg_catalog.pg_am AS access_method
      ON access_method.oid = index_class.relam
    WHERE index_schema.nspname = 'public'
      AND index_class.relname = 'uq_search_sessions_creation_key';

    v_creation_index_found := FOUND;

    SELECT
        index_state.indexrelid AS index_oid,
        index_state.indrelid AS table_oid,
        index_state.indisunique AS is_unique,
        index_state.indisvalid AS is_valid,
        index_state.indisready AS is_ready,
        index_state.indisprimary AS is_primary,
        index_state.indisexclusion AS is_exclusion,
        index_state.indnkeyatts AS key_count,
        index_state.indnatts AS total_attribute_count,
        index_state.indexprs IS NULL AS has_no_expressions,
        access_method.amname AS access_method,
        ARRAY(
            SELECT attribute.attname::TEXT
            FROM unnest(index_state.indkey::SMALLINT[])
                WITH ORDINALITY AS key_column(attnum, position)
            JOIN pg_catalog.pg_attribute AS attribute
              ON attribute.attrelid = index_state.indrelid
             AND attribute.attnum = key_column.attnum
            WHERE key_column.position <= index_state.indnkeyatts
            ORDER BY key_column.position
        ) AS key_columns,
        replace(
            regexp_replace(
                lower(
                    pg_catalog.pg_get_expr(
                        index_state.indpred,
                        index_state.indrelid,
                        FALSE
                    )
                ),
                '[[:space:]()"]+',
                '',
                'g'
            ),
            'public.',
            ''
        ) AS canonical_predicate
    INTO v_open_index
    FROM pg_catalog.pg_index AS index_state
    JOIN pg_catalog.pg_class AS index_class
      ON index_class.oid = index_state.indexrelid
    JOIN pg_catalog.pg_namespace AS index_schema
      ON index_schema.oid = index_class.relnamespace
    JOIN pg_catalog.pg_am AS access_method
      ON access_method.oid = index_class.relam
    WHERE index_schema.nspname = 'public'
      AND index_class.relname = 'uq_search_sessions_one_open_per_user';

    v_open_index_found := FOUND;

    IF NOT v_creation_index_found THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'SEARCH_INDEX_DEFINITION_MISMATCH',
            DETAIL = 'uq_search_sessions_creation_key is missing from public';
    END IF;

    IF v_creation_index.table_oid
          IS DISTINCT FROM 'public.search_sessions'::regclass
       OR v_creation_index.is_unique IS DISTINCT FROM TRUE
       OR v_creation_index.is_valid IS DISTINCT FROM TRUE
       OR v_creation_index.is_ready IS DISTINCT FROM TRUE
       OR v_creation_index.is_primary IS DISTINCT FROM FALSE
       OR v_creation_index.is_exclusion IS DISTINCT FROM FALSE
       OR v_creation_index.access_method IS DISTINCT FROM 'btree'
       OR v_creation_index.key_count IS DISTINCT FROM 1
       OR v_creation_index.total_attribute_count IS DISTINCT FROM 1
       OR v_creation_index.has_no_expressions IS DISTINCT FROM TRUE
       OR v_creation_index.key_columns
          IS DISTINCT FROM ARRAY['creation_key']::TEXT[]
       OR v_creation_index.canonical_predicate
          IS DISTINCT FROM 'creation_keyisnotnull' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'SEARCH_INDEX_DEFINITION_MISMATCH',
            DETAIL = 'uq_search_sessions_creation_key does not match migration 004';
    END IF;

    IF NOT v_open_index_found THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'SEARCH_INDEX_DEFINITION_MISMATCH',
            DETAIL = 'uq_search_sessions_one_open_per_user is missing from public';
    END IF;

    IF v_open_index.table_oid
          IS DISTINCT FROM 'public.search_sessions'::regclass
       OR v_open_index.is_unique IS DISTINCT FROM TRUE
       OR v_open_index.is_valid IS DISTINCT FROM TRUE
       OR v_open_index.is_ready IS DISTINCT FROM TRUE
       OR v_open_index.is_primary IS DISTINCT FROM FALSE
       OR v_open_index.is_exclusion IS DISTINCT FROM FALSE
       OR v_open_index.access_method IS DISTINCT FROM 'btree'
       OR v_open_index.key_count IS DISTINCT FROM 1
       OR v_open_index.total_attribute_count IS DISTINCT FROM 1
       OR v_open_index.has_no_expressions IS DISTINCT FROM TRUE
       OR v_open_index.key_columns
          IS DISTINCT FROM ARRAY['user_id']::TEXT[]
       OR v_open_index.canonical_predicate IS DISTINCT FROM (
           'user_idisnotnullandstatus=anyarray['
           || '''active''::search_status,''paused''::search_status]'
       ) THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'SEARCH_INDEX_DEFINITION_MISMATCH',
            DETAIL = 'uq_search_sessions_one_open_per_user does not match migration 004';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.validate_renter_search_requirements(
    p_listing_types public.listing_type[],
    p_preferred_locations TEXT[],
    p_acceptable_locations TEXT[],
    p_target_rent INTEGER,
    p_max_rent INTEGER,
    p_preferred_move_in_date DATE,
    p_latest_move_in_date DATE,
    p_preferred_property_configurations TEXT[],
    p_core_preferences JSONB,
    p_additional_preferences JSONB
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM unnest(coalesce(p_listing_types, '{}'::public.listing_type[])) AS kind(value)
        WHERE kind.value IS NOT NULL
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'LISTING_TYPE_REQUIRED';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM unnest(
            coalesce(p_preferred_locations, '{}'::TEXT[])
            || coalesce(p_acceptable_locations, '{}'::TEXT[])
        ) AS location(value)
        WHERE btrim(location.value) <> ''
    ) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'LOCATION_REQUIRED';
    END IF;

    IF p_target_rent IS NULL
       OR p_max_rent IS NULL
       OR p_target_rent <= 0
       OR p_max_rent <= 0
       OR p_target_rent > p_max_rent THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'INVALID_RENT_RANGE';
    END IF;

    IF p_preferred_move_in_date IS NULL AND p_latest_move_in_date IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'MOVE_IN_DATE_REQUIRED';
    END IF;

    IF p_preferred_move_in_date IS NOT NULL
       AND p_latest_move_in_date IS NOT NULL
       AND p_preferred_move_in_date > p_latest_move_in_date THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'INVALID_MOVE_IN_WINDOW';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM unnest(
            coalesce(p_preferred_property_configurations, '{}'::TEXT[])
        ) AS configuration(value)
        WHERE configuration.value IS NULL
           OR upper(replace(btrim(configuration.value), ' ', '')) NOT IN (
            '1RK', '1BHK', '2BHK', '3BHK', '4BHK', '4+BHK'
        )
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'INVALID_PROPERTY_CONFIGURATION';
    END IF;

    IF 'ENTIRE_PROPERTY'::public.listing_type = ANY(
        coalesce(p_listing_types, '{}'::public.listing_type[])
    )
       AND coalesce(cardinality(p_preferred_property_configurations), 0) = 0
       AND coalesce(
           p_additional_preferences ->> '__flathunter_configuration_answered',
           'false'
       ) <> 'true' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'INCOMPLETE_REQUIREMENTS';
    END IF;

    IF p_core_preferences IS NULL OR jsonb_typeof(p_core_preferences) <> 'object' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'INVALID_CORE_PREFERENCES';
    END IF;

    IF p_additional_preferences IS NULL OR jsonb_typeof(p_additional_preferences) <> 'object' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'INVALID_ADDITIONAL_PREFERENCES';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.create_renter_search_draft(
    p_creation_key UUID,
    p_user_id UUID,
    p_city TEXT,
    p_listing_types public.listing_type[],
    p_preferred_locations TEXT[],
    p_target_rent INTEGER,
    p_max_rent INTEGER,
    p_acceptable_locations TEXT[] DEFAULT '{}'::TEXT[],
    p_excluded_locations TEXT[] DEFAULT '{}'::TEXT[],
    p_work_location TEXT DEFAULT NULL,
    p_preferred_move_in_date DATE DEFAULT NULL,
    p_latest_move_in_date DATE DEFAULT NULL,
    p_preferred_property_configurations TEXT[] DEFAULT NULL,
    p_core_preferences JSONB DEFAULT '{}'::JSONB,
    p_additional_preferences JSONB DEFAULT '{}'::JSONB,
    p_raw_requirement_text TEXT DEFAULT NULL
)
RETURNS TABLE(session JSONB, created BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_session public.search_sessions%ROWTYPE;
    v_requirements public.search_requirements%ROWTYPE;
    v_created BOOLEAN := FALSE;
BEGIN
    IF p_creation_key IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'CREATION_KEY_REQUIRED';
    END IF;

    IF p_city IS NULL OR btrim(p_city) = '' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'CITY_REQUIRED';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.users WHERE id = p_user_id) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'RENTER_NOT_FOUND';
    END IF;

    PERFORM public.validate_renter_search_requirements(
        p_listing_types,
        p_preferred_locations,
        p_acceptable_locations,
        p_target_rent,
        p_max_rent,
        p_preferred_move_in_date,
        p_latest_move_in_date,
        p_preferred_property_configurations,
        p_core_preferences,
        p_additional_preferences
    );

    INSERT INTO public.search_sessions (
        user_id,
        status,
        version,
        city,
        creation_key
    )
    VALUES (
        p_user_id,
        'DRAFT',
        1,
        btrim(p_city),
        p_creation_key
    )
    ON CONFLICT (creation_key) WHERE creation_key IS NOT NULL DO NOTHING
    RETURNING * INTO v_session;

    IF FOUND THEN
        v_created := TRUE;

        INSERT INTO public.search_requirements (
            search_id,
            listing_types,
            preferred_locations,
            acceptable_locations,
            excluded_locations,
            work_location,
            target_rent,
            max_rent,
            preferred_move_in_date,
            latest_move_in_date,
            preferred_property_configurations,
            core_preferences,
            additional_preferences,
            raw_requirement_text
        )
        VALUES (
            v_session.id,
            p_listing_types,
            p_preferred_locations,
            coalesce(p_acceptable_locations, '{}'::TEXT[]),
            coalesce(p_excluded_locations, '{}'::TEXT[]),
            nullif(btrim(p_work_location), ''),
            p_target_rent,
            p_max_rent,
            p_preferred_move_in_date,
            p_latest_move_in_date,
            p_preferred_property_configurations,
            p_core_preferences,
            p_additional_preferences,
            p_raw_requirement_text
        );
    ELSE
        SELECT * INTO v_session
        FROM public.search_sessions
        WHERE creation_key = p_creation_key
        FOR UPDATE;

        IF NOT FOUND OR v_session.user_id IS DISTINCT FROM p_user_id THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'CREATION_KEY_CONFLICT';
        END IF;

        IF v_session.status <> 'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'CREATION_KEY_CONFLICT';
        END IF;

        SELECT * INTO v_requirements
        FROM public.search_requirements
        WHERE search_id = v_session.id;

        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_REQUIREMENTS_MISSING';
        END IF;

        IF v_session.city IS DISTINCT FROM btrim(p_city)
           OR v_requirements.listing_types IS DISTINCT FROM p_listing_types
           OR v_requirements.preferred_locations IS DISTINCT FROM p_preferred_locations
           OR v_requirements.acceptable_locations IS DISTINCT FROM coalesce(p_acceptable_locations, '{}'::TEXT[])
           OR v_requirements.excluded_locations IS DISTINCT FROM coalesce(p_excluded_locations, '{}'::TEXT[])
           OR v_requirements.work_location IS DISTINCT FROM nullif(btrim(p_work_location), '')
           OR v_requirements.target_rent IS DISTINCT FROM p_target_rent
           OR v_requirements.max_rent IS DISTINCT FROM p_max_rent
           OR v_requirements.preferred_move_in_date IS DISTINCT FROM p_preferred_move_in_date
           OR v_requirements.latest_move_in_date IS DISTINCT FROM p_latest_move_in_date
           OR v_requirements.preferred_property_configurations IS DISTINCT FROM p_preferred_property_configurations
           OR v_requirements.core_preferences IS DISTINCT FROM p_core_preferences
           OR v_requirements.additional_preferences IS DISTINCT FROM p_additional_preferences
           OR v_requirements.raw_requirement_text IS DISTINCT FROM p_raw_requirement_text THEN
            RAISE EXCEPTION USING
                ERRCODE = 'P0001',
                MESSAGE = 'CREATION_KEY_PAYLOAD_MISMATCH';
        END IF;
    END IF;

    RETURN QUERY SELECT to_jsonb(v_session), v_created;
END;
$$;

CREATE OR REPLACE FUNCTION public.get_renter_search_draft_by_creation_key(
    p_user_id UUID,
    p_creation_key UUID
)
RETURNS TABLE(session JSONB, requirements JSONB)
LANGUAGE sql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
    SELECT to_jsonb(search_session), to_jsonb(search_requirement)
    FROM public.search_sessions AS search_session
    JOIN public.search_requirements AS search_requirement
      ON search_requirement.search_id = search_session.id
    WHERE search_session.user_id = p_user_id
      AND search_session.creation_key = p_creation_key
      AND search_session.status = 'DRAFT'
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.update_renter_search_draft(
    p_user_id UUID,
    p_search_id UUID,
    p_expected_version INTEGER,
    p_listing_types public.listing_type[],
    p_preferred_locations TEXT[],
    p_target_rent INTEGER,
    p_max_rent INTEGER,
    p_acceptable_locations TEXT[] DEFAULT '{}'::TEXT[],
    p_excluded_locations TEXT[] DEFAULT '{}'::TEXT[],
    p_work_location TEXT DEFAULT NULL,
    p_preferred_move_in_date DATE DEFAULT NULL,
    p_latest_move_in_date DATE DEFAULT NULL,
    p_preferred_property_configurations TEXT[] DEFAULT NULL,
    p_core_preferences JSONB DEFAULT '{}'::JSONB,
    p_additional_preferences JSONB DEFAULT '{}'::JSONB,
    p_raw_requirement_text TEXT DEFAULT NULL
)
RETURNS TABLE(session JSONB, updated BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_session public.search_sessions%ROWTYPE;
    v_requirements_id UUID;
BEGIN
    -- Use the same user-first lock order as activation so an extras update
    -- cannot race the Start action for this draft.
    PERFORM 1
    FROM public.users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'RENTER_NOT_FOUND';
    END IF;

    SELECT * INTO v_session
    FROM public.search_sessions
    WHERE id = p_search_id
      AND user_id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_FOUND';
    END IF;

    IF p_expected_version IS NULL
       OR p_expected_version <= 0
       OR v_session.version <> p_expected_version THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'STALE_SEARCH_VERSION';
    END IF;

    IF v_session.status <> 'DRAFT' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_DRAFT';
    END IF;

    PERFORM public.validate_renter_search_requirements(
        p_listing_types,
        p_preferred_locations,
        p_acceptable_locations,
        p_target_rent,
        p_max_rent,
        p_preferred_move_in_date,
        p_latest_move_in_date,
        p_preferred_property_configurations,
        p_core_preferences,
        p_additional_preferences
    );

    UPDATE public.search_requirements
    SET listing_types = p_listing_types,
        preferred_locations = p_preferred_locations,
        acceptable_locations = coalesce(p_acceptable_locations, '{}'::TEXT[]),
        excluded_locations = coalesce(p_excluded_locations, '{}'::TEXT[]),
        work_location = nullif(btrim(p_work_location), ''),
        target_rent = p_target_rent,
        max_rent = p_max_rent,
        preferred_move_in_date = p_preferred_move_in_date,
        latest_move_in_date = p_latest_move_in_date,
        preferred_property_configurations = p_preferred_property_configurations,
        core_preferences = p_core_preferences,
        additional_preferences = p_additional_preferences,
        raw_requirement_text = p_raw_requirement_text,
        updated_at = now()
    WHERE search_id = p_search_id
    RETURNING id INTO v_requirements_id;

    IF v_requirements_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_REQUIREMENTS_MISSING';
    END IF;

    UPDATE public.search_sessions
    SET version = version + 1,
        updated_at = now()
    WHERE id = p_search_id
    RETURNING * INTO v_session;

    RETURN QUERY SELECT to_jsonb(v_session), TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.activate_renter_search(
    p_user_id UUID,
    p_search_id UUID,
    p_expected_version INTEGER,
    p_replace_search_id UUID DEFAULT NULL,
    p_replace_expected_version INTEGER DEFAULT NULL
)
RETURNS TABLE(
    session JSONB,
    activated BOOLEAN,
    job_enqueued BOOLEAN,
    replaced_search_id UUID
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_session public.search_sessions%ROWTYPE;
    v_open_search public.search_sessions%ROWTYPE;
    v_requirements public.search_requirements%ROWTYPE;
    v_job_id UUID;
    v_activated BOOLEAN := FALSE;
    v_replaced_search_id UUID := NULL;
BEGIN
    -- Serialize activation of different drafts owned by the same renter before
    -- either transaction takes a target-session lock.
    PERFORM 1
    FROM public.users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'RENTER_NOT_FOUND';
    END IF;

    SELECT * INTO v_session
    FROM public.search_sessions
    WHERE id = p_search_id
      AND user_id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_FOUND';
    END IF;

    IF p_expected_version IS NULL
       OR p_expected_version <= 0
       OR v_session.version <> p_expected_version THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'STALE_SEARCH_VERSION';
    END IF;

    IF v_session.status NOT IN ('DRAFT', 'ACTIVE') THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_ACTIVATABLE';
    END IF;

    SELECT * INTO v_requirements
    FROM public.search_requirements
    WHERE search_id = p_search_id
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_REQUIREMENTS_MISSING';
    END IF;

    PERFORM public.validate_renter_search_requirements(
        v_requirements.listing_types,
        v_requirements.preferred_locations,
        v_requirements.acceptable_locations,
        v_requirements.target_rent,
        v_requirements.max_rent,
        v_requirements.preferred_move_in_date,
        v_requirements.latest_move_in_date,
        v_requirements.preferred_property_configurations,
        v_requirements.core_preferences,
        v_requirements.additional_preferences
    );

    SELECT * INTO v_open_search
    FROM public.search_sessions
    WHERE user_id = p_user_id
      AND id <> p_search_id
      AND status IN ('ACTIVE', 'PAUSED')
    FOR UPDATE;

    IF FOUND THEN
        IF p_replace_search_id IS NULL OR v_open_search.id <> p_replace_search_id THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'ACTIVE_SEARCH_EXISTS';
        END IF;

        IF p_replace_expected_version IS NULL
           OR v_open_search.version <> p_replace_expected_version THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'STALE_REPLACEMENT_VERSION';
        END IF;

        UPDATE public.search_sessions
        SET status = 'CLOSED',
            closed_at = now(),
            updated_at = now()
        WHERE id = v_open_search.id;

        v_replaced_search_id := v_open_search.id;
    END IF;

    IF v_session.status = 'DRAFT' THEN
        UPDATE public.search_sessions
        SET status = 'ACTIVE',
            started_at = coalesce(started_at, now()),
            last_activated_at = now(),
            updated_at = now()
        WHERE id = p_search_id
        RETURNING * INTO v_session;

        v_activated := TRUE;
    END IF;

    INSERT INTO public.agent_jobs (
        job_type,
        idempotency_key,
        status,
        payload,
        run_after
    )
    VALUES (
        'MATCH_ACTIVE_SEARCH',
        'MATCH_ACTIVE_SEARCH:' || p_search_id::TEXT || ':' || v_session.version::TEXT,
        'PENDING',
        jsonb_build_object(
            'search_id', p_search_id::TEXT,
            'search_version', v_session.version,
            'trigger', 'SEARCH_STARTED'
        ),
        now()
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO v_job_id;

    RETURN QUERY
    SELECT
        to_jsonb(v_session),
        v_activated,
        v_job_id IS NOT NULL,
        v_replaced_search_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.cancel_renter_searches(
    p_user_id UUID,
    p_draft_search_id UUID,
    p_draft_expected_version INTEGER,
    p_open_search_id UUID,
    p_open_expected_version INTEGER
)
RETURNS TABLE(
    draft_session JSONB,
    open_session JSONB,
    cancelled_count INTEGER
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_draft_session public.search_sessions%ROWTYPE;
    v_open_session public.search_sessions%ROWTYPE;
    v_cancelled_count INTEGER := 0;
BEGIN
    IF p_draft_search_id IS NULL AND p_open_search_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'CANCEL_TARGET_REQUIRED';
    END IF;

    IF p_draft_search_id IS NULL AND p_draft_expected_version IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'DRAFT_ID_REQUIRED';
    END IF;

    IF p_draft_search_id IS NOT NULL
       AND (p_draft_expected_version IS NULL OR p_draft_expected_version <= 0) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'DRAFT_VERSION_REQUIRED';
    END IF;

    IF p_open_search_id IS NULL AND p_open_expected_version IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'OPEN_SEARCH_ID_REQUIRED';
    END IF;

    IF p_open_search_id IS NOT NULL
       AND (p_open_expected_version IS NULL OR p_open_expected_version <= 0) THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'OPEN_SEARCH_VERSION_REQUIRED';
    END IF;

    IF p_draft_search_id IS NOT NULL
       AND p_open_search_id IS NOT NULL
       AND p_draft_search_id = p_open_search_id THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'CANCEL_TARGET_CONFLICT';
    END IF;

    -- Serialize all lifecycle changes for one renter before locking targets.
    PERFORM 1
    FROM public.users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'RENTER_NOT_FOUND';
    END IF;

    -- Validate every selected row and expected version before either UPDATE.
    IF p_draft_search_id IS NOT NULL THEN
        SELECT * INTO v_draft_session
        FROM public.search_sessions
        WHERE id = p_draft_search_id
          AND user_id = p_user_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'DRAFT_SEARCH_NOT_FOUND';
        END IF;

        IF v_draft_session.version <> p_draft_expected_version THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'STALE_DRAFT_VERSION';
        END IF;

        IF v_draft_session.status <> 'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_DRAFT';
        END IF;
    END IF;

    IF p_open_search_id IS NOT NULL THEN
        SELECT * INTO v_open_session
        FROM public.search_sessions
        WHERE id = p_open_search_id
          AND user_id = p_user_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'OPEN_SEARCH_NOT_FOUND';
        END IF;

        IF v_open_session.version <> p_open_expected_version THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'STALE_OPEN_SEARCH_VERSION';
        END IF;

        IF v_open_session.status NOT IN ('ACTIVE', 'PAUSED') THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_OPEN';
        END IF;
    END IF;

    IF p_draft_search_id IS NOT NULL THEN
        UPDATE public.search_sessions
        SET status = 'CLOSED',
            version = version + 1,
            closed_at = now(),
            updated_at = now()
        WHERE id = p_draft_search_id
        RETURNING * INTO v_draft_session;
        v_cancelled_count := v_cancelled_count + 1;
    END IF;

    IF p_open_search_id IS NOT NULL THEN
        UPDATE public.search_sessions
        SET status = 'CLOSED',
            version = version + 1,
            closed_at = now(),
            updated_at = now()
        WHERE id = p_open_search_id
        RETURNING * INTO v_open_session;
        v_cancelled_count := v_cancelled_count + 1;
    END IF;

    RETURN QUERY SELECT
        CASE WHEN p_draft_search_id IS NULL THEN NULL ELSE to_jsonb(v_draft_session) END,
        CASE WHEN p_open_search_id IS NULL THEN NULL ELSE to_jsonb(v_open_session) END,
        v_cancelled_count;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_renter_search_paused(
    p_user_id UUID,
    p_search_id UUID,
    p_expected_version INTEGER,
    p_paused BOOLEAN
)
RETURNS TABLE(session JSONB, changed BOOLEAN, job_enqueued BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_session public.search_sessions%ROWTYPE;
    v_job_id UUID;
BEGIN
    IF p_paused IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'PAUSED_FLAG_REQUIRED';
    END IF;

    PERFORM 1
    FROM public.users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'RENTER_NOT_FOUND';
    END IF;

    SELECT * INTO v_session
    FROM public.search_sessions
    WHERE id = p_search_id
      AND user_id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_FOUND';
    END IF;

    IF p_expected_version IS NULL
       OR p_expected_version <= 0
       OR v_session.version <> p_expected_version THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'STALE_SEARCH_VERSION';
    END IF;

    IF p_paused THEN
        IF v_session.status = 'PAUSED' THEN
            RETURN QUERY SELECT to_jsonb(v_session), FALSE, FALSE;
            RETURN;
        END IF;
        IF v_session.status <> 'ACTIVE' THEN
            RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_ACTIVE';
        END IF;

        UPDATE public.search_sessions
        SET status = 'PAUSED',
            version = version + 1,
            paused_at = now(),
            updated_at = now()
        WHERE id = p_search_id
        RETURNING * INTO v_session;

        RETURN QUERY SELECT to_jsonb(v_session), TRUE, FALSE;
        RETURN;
    END IF;

    IF v_session.status = 'ACTIVE' THEN
        RETURN QUERY SELECT to_jsonb(v_session), FALSE, FALSE;
        RETURN;
    END IF;
    IF v_session.status <> 'PAUSED' THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_PAUSED';
    END IF;

    UPDATE public.search_sessions
    SET status = 'ACTIVE',
        version = version + 1,
        paused_at = NULL,
        last_activated_at = now(),
        updated_at = now()
    WHERE id = p_search_id
    RETURNING * INTO v_session;

    INSERT INTO public.agent_jobs (
        job_type,
        idempotency_key,
        status,
        payload,
        run_after
    )
    VALUES (
        'MATCH_ACTIVE_SEARCH',
        'MATCH_ACTIVE_SEARCH:' || p_search_id::TEXT || ':' || v_session.version::TEXT,
        'PENDING',
        jsonb_build_object(
            'search_id', p_search_id::TEXT,
            'search_version', v_session.version,
            'trigger', 'RESUMED'
        ),
        now()
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO v_job_id;

    IF v_job_id IS NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = 'P0001',
            MESSAGE = 'RESUME_JOB_CONFLICT';
    END IF;

    RETURN QUERY SELECT to_jsonb(v_session), TRUE, TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION public.update_live_renter_search(
    p_user_id UUID,
    p_search_id UUID,
    p_expected_version INTEGER,
    p_listing_types public.listing_type[],
    p_preferred_locations TEXT[],
    p_target_rent INTEGER,
    p_max_rent INTEGER,
    p_acceptable_locations TEXT[],
    p_excluded_locations TEXT[],
    p_work_location TEXT,
    p_preferred_move_in_date DATE,
    p_latest_move_in_date DATE,
    p_preferred_property_configurations TEXT[],
    p_core_preferences JSONB,
    p_additional_preferences JSONB,
    p_raw_requirement_text TEXT
)
RETURNS TABLE(
    session JSONB,
    requirements JSONB,
    job_enqueued BOOLEAN
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_session public.search_sessions%ROWTYPE;
    v_requirements public.search_requirements%ROWTYPE;
    v_job_id UUID;
BEGIN
    IF p_expected_version IS NULL OR p_expected_version <= 0 THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'EXPECTED_VERSION_REQUIRED';
    END IF;

    PERFORM 1
    FROM public.users
    WHERE id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'RENTER_NOT_FOUND';
    END IF;

    SELECT * INTO v_session
    FROM public.search_sessions
    WHERE id = p_search_id
      AND user_id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_FOUND';
    END IF;

    IF v_session.version <> p_expected_version THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'STALE_SEARCH_VERSION';
    END IF;

    IF v_session.status NOT IN ('ACTIVE', 'PAUSED') THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_NOT_EDITABLE';
    END IF;

    SELECT * INTO v_requirements
    FROM public.search_requirements
    WHERE search_id = p_search_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'SEARCH_REQUIREMENTS_MISSING';
    END IF;

    PERFORM public.validate_renter_search_requirements(
        p_listing_types,
        p_preferred_locations,
        p_acceptable_locations,
        p_target_rent,
        p_max_rent,
        p_preferred_move_in_date,
        p_latest_move_in_date,
        p_preferred_property_configurations,
        p_core_preferences,
        p_additional_preferences
    );

    UPDATE public.search_requirements
    SET listing_types = p_listing_types,
        preferred_locations = p_preferred_locations,
        acceptable_locations = coalesce(p_acceptable_locations, '{}'::TEXT[]),
        excluded_locations = coalesce(p_excluded_locations, '{}'::TEXT[]),
        work_location = nullif(btrim(p_work_location), ''),
        target_rent = p_target_rent,
        max_rent = p_max_rent,
        preferred_move_in_date = p_preferred_move_in_date,
        latest_move_in_date = p_latest_move_in_date,
        preferred_property_configurations = p_preferred_property_configurations,
        core_preferences = p_core_preferences,
        additional_preferences = p_additional_preferences,
        raw_requirement_text = p_raw_requirement_text,
        updated_at = now()
    WHERE search_id = p_search_id
    RETURNING * INTO v_requirements;

    UPDATE public.search_sessions
    SET version = version + 1,
        updated_at = now()
    WHERE id = p_search_id
    RETURNING * INTO v_session;

    INSERT INTO public.agent_jobs (
        job_type,
        idempotency_key,
        status,
        payload,
        run_after
    )
    VALUES (
        'SEARCH_UPDATED',
        'SEARCH_UPDATED:' || p_search_id::TEXT || ':' || v_session.version::TEXT,
        'PENDING',
        jsonb_build_object(
            'search_id', p_search_id::TEXT,
            'search_version', v_session.version,
            'trigger', 'SEARCH_UPDATED'
        ),
        now()
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO v_job_id;

    RETURN QUERY SELECT to_jsonb(v_session), to_jsonb(v_requirements), v_job_id IS NOT NULL;
END;
$$;

-- Recover jobs abandoned by a dead worker before claiming the next due item.
CREATE OR REPLACE FUNCTION public.claim_next_agent_job(p_worker_id TEXT)
RETURNS SETOF public.agent_jobs
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public, pg_temp
AS $$
DECLARE
    claimed_job public.agent_jobs%ROWTYPE;
BEGIN
    UPDATE public.agent_jobs
    SET status = 'FAILED',
        completed_at = now(),
        last_error = coalesce(last_error, 'Worker lock expired after final attempt'),
        locked_at = NULL,
        locked_by = NULL,
        updated_at = now()
    WHERE status = 'RUNNING'
      AND locked_at < now() - interval '10 minutes'
      AND attempts >= 3;

    UPDATE public.agent_jobs
    SET status = 'PENDING',
        run_after = now(),
        last_error = coalesce(last_error, 'Worker lock expired before completion'),
        locked_at = NULL,
        locked_by = NULL,
        updated_at = now()
    WHERE status = 'RUNNING'
      AND locked_at < now() - interval '10 minutes'
      AND attempts < 3;

    SELECT * INTO claimed_job
    FROM public.agent_jobs
    WHERE status = 'PENDING'
      AND run_after <= now()
    ORDER BY run_after, created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    UPDATE public.agent_jobs
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

REVOKE ALL PRIVILEGES ON FUNCTION public.validate_renter_search_requirements(
    public.listing_type[], TEXT[], TEXT[], INTEGER, INTEGER, DATE, DATE, TEXT[], JSONB, JSONB
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.validate_renter_search_requirements(
    public.listing_type[], TEXT[], TEXT[], INTEGER, INTEGER, DATE, DATE, TEXT[], JSONB, JSONB
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.create_renter_search_draft(
    UUID, UUID, TEXT, public.listing_type[], TEXT[], INTEGER, INTEGER,
    TEXT[], TEXT[], TEXT, DATE, DATE, TEXT[], JSONB, JSONB, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_renter_search_draft(
    UUID, UUID, TEXT, public.listing_type[], TEXT[], INTEGER, INTEGER,
    TEXT[], TEXT[], TEXT, DATE, DATE, TEXT[], JSONB, JSONB, TEXT
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.get_renter_search_draft_by_creation_key(
    UUID, UUID
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.get_renter_search_draft_by_creation_key(
    UUID, UUID
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.update_renter_search_draft(
    UUID, UUID, INTEGER, public.listing_type[], TEXT[], INTEGER, INTEGER,
    TEXT[], TEXT[], TEXT, DATE, DATE, TEXT[], JSONB, JSONB, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_renter_search_draft(
    UUID, UUID, INTEGER, public.listing_type[], TEXT[], INTEGER, INTEGER,
    TEXT[], TEXT[], TEXT, DATE, DATE, TEXT[], JSONB, JSONB, TEXT
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.activate_renter_search(
    UUID, UUID, INTEGER, UUID, INTEGER
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.activate_renter_search(
    UUID, UUID, INTEGER, UUID, INTEGER
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.cancel_renter_searches(
    UUID, UUID, INTEGER, UUID, INTEGER
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.cancel_renter_searches(
    UUID, UUID, INTEGER, UUID, INTEGER
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.set_renter_search_paused(
    UUID, UUID, INTEGER, BOOLEAN
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_renter_search_paused(
    UUID, UUID, INTEGER, BOOLEAN
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.update_live_renter_search(
    UUID, UUID, INTEGER, public.listing_type[], TEXT[], INTEGER, INTEGER,
    TEXT[], TEXT[], TEXT, DATE, DATE, TEXT[], JSONB, JSONB, TEXT
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.update_live_renter_search(
    UUID, UUID, INTEGER, public.listing_type[], TEXT[], INTEGER, INTEGER,
    TEXT[], TEXT[], TEXT, DATE, DATE, TEXT[], JSONB, JSONB, TEXT
) TO service_role;

REVOKE ALL PRIVILEGES ON FUNCTION public.claim_next_agent_job(TEXT)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_next_agent_job(TEXT)
    TO service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
