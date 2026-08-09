-- DANGER: Irreversibly deletes every FlatHunter table and all data in them.
-- It intentionally leaves Supabase-managed schemas such as auth and storage alone.
--
-- Before running:
--   1. Export/backup any data you may need.
--   2. Verify you are connected to the intended Supabase project/database.
--   3. Change the confirmation value below from KEEP_DATA to DELETE_FLATHUNTER.
--
-- After this succeeds, recreate the application schema by running, in order:
--   migrations/001_initial_schema.sql
--   migrations/002_add_content_type_other.sql

BEGIN;

DO $$
DECLARE
    confirmation CONSTANT text := 'KEEP_DATA';
BEGIN
    IF confirmation <> 'DELETE_FLATHUNTER' THEN
        RAISE EXCEPTION
            'Reset cancelled. Change confirmation to DELETE_FLATHUNTER only after verifying the target database and backup.';
    END IF;
END
$$;

DROP FUNCTION IF EXISTS public.claim_next_agent_job(text);

-- CASCADE removes foreign-key constraints, indexes, and dependent objects owned
-- by these FlatHunter tables. Supabase platform tables are not named here.
DROP TABLE IF EXISTS
    public.model_calls,
    public.agent_jobs,
    public.visits,
    public.outreach_attempts,
    public.messages,
    public.conversations,
    public.matches,
    public.contact_channels,
    public.contacts,
    public.listing_media,
    public.listing_sources,
    public.listings,
    public.listing_drafts,
    public.ingestion_inputs,
    public.ingestion_sessions,
    public.renter_availability,
    public.search_requirements,
    public.search_sessions,
    public.users
CASCADE;

DROP TYPE IF EXISTS
    public.ingestion_status,
    public.contact_channel_type,
    public.visit_status,
    public.conversation_status,
    public.match_status,
    public.preference_importance,
    public.availability_status,
    public.content_type,
    public.listing_type,
    public.search_status,
    public.user_role
CASCADE;

COMMIT;
