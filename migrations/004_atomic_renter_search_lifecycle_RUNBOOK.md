# Migration 004 manual rollout

This runbook applies [`004_atomic_renter_search_lifecycle.sql`](./004_atomic_renter_search_lifecycle.sql) manually in the target Supabase project. Nothing in this document runs automatically. Run it first in a non-production project when one is available, and take a database backup before a production rollout.

Stop the bot and job worker during the rollout. Deploy application code that calls the new RPCs only after the post-migration checks pass.

## 1. Preflight

Run each read-only query in Supabase SQL Editor.

1. The migration requires at most one open (`ACTIVE` or `PAUSED`) search per renter. This query **must return zero rows**. If it returns rows, review them manually; migration 004 will abort with `OPEN_SEARCH_PRECHECK_FAILED` and make no changes.

```sql
SELECT
    user_id,
    count(*) AS open_search_count,
    jsonb_agg(
        jsonb_build_object(
            'id', id,
            'status', status,
            'version', version,
            'created_at', created_at
        )
        ORDER BY created_at DESC, id
    ) AS open_searches
FROM public.search_sessions
WHERE user_id IS NOT NULL
  AND status IN ('ACTIVE', 'PAUSED')
GROUP BY user_id
HAVING count(*) > 1
ORDER BY user_id;
```

2. Record these row counts. With the bot and worker stopped, the same query after migration should return the same values because migration 004 does not rewrite application data.

```sql
SELECT
    (SELECT count(*) FROM public.search_sessions) AS search_sessions,
    (SELECT count(*) FROM public.search_requirements) AS search_requirements,
    (SELECT count(*) FROM public.matches) AS matches,
    (SELECT count(*) FROM public.agent_jobs) AS agent_jobs;
```

3. Check whether migration 004 was already applied. Zero rows means it has not added `creation_key`. If a row is returned, it must be nullable `uuid`; stop and investigate any other shape.

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'search_sessions'
  AND column_name = 'creation_key';
```

4. Only when query 3 returned the expected column, check its populated values. This query must return zero rows before the unique creation-key index can be created.

```sql
SELECT creation_key, count(*) AS occurrence_count
FROM public.search_sessions
WHERE creation_key IS NOT NULL
GROUP BY creation_key
HAVING count(*) > 1
ORDER BY creation_key;
```

## 2. Apply the migration

1. Open [`004_atomic_renter_search_lifecycle.sql`](./004_atomic_renter_search_lifecycle.sql).
2. Copy the **entire file**, from `BEGIN;` through `COMMIT;`, into a new Supabase SQL Editor query.
3. Confirm that the editor is connected to the intended project, then run it once.
4. Do not run individual function fragments. The transaction ensures that a failure rolls back the whole migration.
5. If the editor reports `OPEN_SEARCH_PRECHECK_FAILED`, return to preflight query 1 and resolve the duplicate open searches deliberately. Do not delete or close one based only on recency.

## 3. Post-migration verification

1. Confirm that the new column and both valid indexes exist.

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'search_sessions'
  AND column_name = 'creation_key';

SELECT
    index_class.relname AS index_name,
    table_class.relname AS table_name,
    index_state.indisunique AS is_unique,
    index_state.indisvalid AS is_valid,
    index_state.indisready AS is_ready,
    pg_get_expr(index_state.indpred, index_state.indrelid) AS predicate,
    pg_get_indexdef(index_state.indexrelid) AS definition
FROM pg_index AS index_state
JOIN pg_class AS index_class
  ON index_class.oid = index_state.indexrelid
JOIN pg_class AS table_class
  ON table_class.oid = index_state.indrelid
JOIN pg_namespace AS index_schema
  ON index_schema.oid = index_class.relnamespace
WHERE index_schema.nspname = 'public'
  AND table_class.relname = 'search_sessions'
  AND index_class.relname IN (
      'uq_search_sessions_creation_key',
      'uq_search_sessions_one_open_per_user'
  )
ORDER BY index_class.relname;
```

Run this exact definition assertion as well. It rejects a same-name index on a
different table, a non-unique/invalid/not-ready index, a different access
method, expression or included columns, and predicates with missing or extra
clauses:

```sql
WITH expected(index_name, key_columns, canonical_predicate) AS (
    VALUES
        (
            'uq_search_sessions_creation_key',
            ARRAY['creation_key']::text[],
            'creation_keyisnotnull'
        ),
        (
            'uq_search_sessions_one_open_per_user',
            ARRAY['user_id']::text[],
            'user_idisnotnullandstatus=anyarray['
            || '''active''::search_status,''paused''::search_status]'
        )
),
actual AS (
    SELECT
        index_class.relname AS index_name,
        index_state.indrelid AS table_oid,
        index_state.indisunique,
        index_state.indisvalid,
        index_state.indisready,
        index_state.indisprimary,
        index_state.indisexclusion,
        index_state.indnkeyatts,
        index_state.indnatts,
        index_state.indexprs IS NULL AS has_no_expressions,
        access_method.amname AS access_method,
        ARRAY(
            SELECT attribute.attname::text
            FROM unnest(index_state.indkey::smallint[])
                WITH ORDINALITY AS key_column(attnum, position)
            JOIN pg_catalog.pg_attribute AS attribute
              ON attribute.attrelid = index_state.indrelid
             AND attribute.attnum = key_column.attnum
            WHERE key_column.position <= index_state.indnkeyatts
            ORDER BY key_column.position
        ) AS key_columns,
        pg_catalog.pg_get_expr(
            index_state.indpred,
            index_state.indrelid,
            false
        ) AS predicate,
        replace(
            regexp_replace(
                lower(
                    pg_catalog.pg_get_expr(
                        index_state.indpred,
                        index_state.indrelid,
                        false
                    )
                ),
                '[[:space:]()"]+',
                '',
                'g'
            ),
            'public.',
            ''
        ) AS canonical_predicate
    FROM pg_catalog.pg_index AS index_state
    JOIN pg_catalog.pg_class AS index_class
      ON index_class.oid = index_state.indexrelid
    JOIN pg_catalog.pg_class AS table_class
      ON table_class.oid = index_state.indrelid
    JOIN pg_catalog.pg_namespace AS table_schema
      ON table_schema.oid = table_class.relnamespace
    JOIN pg_catalog.pg_am AS access_method
      ON access_method.oid = index_class.relam
    WHERE table_schema.nspname = 'public'
      AND table_class.relname = 'search_sessions'
      AND index_class.relname IN (
          'uq_search_sessions_creation_key',
          'uq_search_sessions_one_open_per_user'
      )
)
SELECT
    expected.index_name,
    actual.index_name IS NOT NULL AS exists_on_search_sessions,
    coalesce(actual.table_oid = 'public.search_sessions'::regclass, false)
        AS is_on_search_sessions,
    coalesce(actual.access_method = 'btree', false) AS is_btree,
    coalesce(actual.indisunique, false) AS is_unique,
    coalesce(actual.indisvalid, false) AS is_valid,
    coalesce(actual.indisready, false) AS is_ready,
    coalesce(NOT actual.indisprimary, false) AS is_not_primary,
    coalesce(NOT actual.indisexclusion, false) AS is_not_exclusion,
    coalesce(
        actual.indnkeyatts = 1
        AND actual.indnatts = 1
        AND actual.has_no_expressions
        AND actual.key_columns = expected.key_columns,
        false
    ) AS has_exact_single_plain_key,
    coalesce(
        actual.canonical_predicate = expected.canonical_predicate,
        false
    ) AS predicate_matches_exactly,
    actual.canonical_predicate,
    actual.predicate
FROM expected
LEFT JOIN actual USING (index_name)
ORDER BY expected.index_name;
```

Expected: creation_key is nullable uuid; this assertion returns exactly two
rows, and every boolean column is true. Predicate equality is intentional:
substring checks can miss an extra clause that silently weakens the uniqueness
guarantee. Migration 004 performs the same check and aborts with
`SEARCH_INDEX_DEFINITION_MISMATCH` if a pre-existing same-name index is not
the exact intended index.

2. Confirm that all nine functions exist exactly once, are security-invoker functions, and have the fixed search path. exactly_one, security_invoker, and fixed_search_path must all be true.

```sql
WITH expected(function_name) AS (
    VALUES
        ('validate_renter_search_requirements'),
        ('create_renter_search_draft'),
        ('get_renter_search_draft_by_creation_key'),
        ('update_renter_search_draft'),
        ('activate_renter_search'),
        ('cancel_renter_searches'),
        ('set_renter_search_paused'),
        ('update_live_renter_search'),
        ('claim_next_agent_job')
),
actual AS (
    SELECT function_row.*
    FROM pg_proc AS function_row
    JOIN pg_namespace AS function_schema
      ON function_schema.oid = function_row.pronamespace
    WHERE function_schema.nspname = 'public'
)
SELECT
    expected.function_name,
    count(actual.oid) = 1 AS exactly_one,
    coalesce(bool_and(NOT actual.prosecdef), false) AS security_invoker,
    coalesce(
        bool_and(
            'search_path=public, pg_temp'
                = ANY(coalesce(actual.proconfig, ARRAY[]::text[]))
        ),
        false
    ) AS fixed_search_path,
    string_agg(
        pg_get_function_identity_arguments(actual.oid),
        ' | '
        ORDER BY pg_get_function_identity_arguments(actual.oid)
    ) AS arguments
FROM expected
LEFT JOIN actual
  ON actual.proname = expected.function_name
GROUP BY expected.function_name
ORDER BY expected.function_name;
```

3. Confirm that execution is granted only to service_role among the API roles. This query must return exactly nine service_role rows and no PUBLIC, anon, or authenticated rows.

```sql
SELECT grantee, routine_name, privilege_type
FROM information_schema.routine_privileges
WHERE routine_schema = 'public'
  AND routine_name IN (
      'validate_renter_search_requirements',
      'create_renter_search_draft',
      'get_renter_search_draft_by_creation_key',
      'update_renter_search_draft',
      'activate_renter_search',
      'cancel_renter_searches',
      'set_renter_search_paused',
      'update_live_renter_search',
      'claim_next_agent_job'
  )
  AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role')
ORDER BY routine_name, grantee;
```

4. Rerun preflight queries 1 and 2. There should still be no duplicate open searches, and the four recorded row counts should be unchanged. Do **not** invoke `claim_next_agent_job` merely to test it: calling it claims or recovers jobs and therefore changes data.

5. Restart/deploy the application only after these checks pass. In a non-production project, exercise the RPCs through normal application paths:

   - create a draft and retry with the same creation key; the lookup RPC must return the same owned draft without changing its version;
   - update the draft, then activate it and verify exactly one MATCH_ACTIVE_SEARCH:<search_id>:<version> job;
   - pause and resume the open search with version checks;
   - edit the live requirements and verify its version advances exactly once with one SEARCH_UPDATED:<search_id>:<version> job;
   - cancel a disposable draft, an open search, and both together, verifying that a stale version leaves every selected row unchanged.

   Do not invoke claim_next_agent_job by hand. Its behavior is verified by the restarted worker because a manual call claims or recovers jobs and changes data.

## 4. Read-only draft audit

This query reports orphaned or invalid `DRAFT` sessions using the same core validity rules as migration 004. `eligible_for_optional_cleanup` is true only when no downstream row or queued job references that draft.

```sql
WITH draft_issues AS (
    SELECT
        session_row.id AS search_id,
        session_row.user_id,
        session_row.version,
        session_row.creation_key,
        session_row.created_at,
        array_remove(ARRAY[
            CASE WHEN session_row.user_id IS NULL THEN 'RENTER_REQUIRED' END,
            CASE WHEN btrim(session_row.city) = '' THEN 'CITY_REQUIRED' END,
            CASE WHEN requirement_row.search_id IS NULL THEN 'SEARCH_REQUIREMENTS_MISSING' END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1
                     FROM unnest(
                         coalesce(
                             requirement_row.listing_types,
                             '{}'::public.listing_type[]
                         )
                     ) AS listing_kind(value)
                     WHERE listing_kind.value IS NOT NULL
                 )
                THEN 'LISTING_TYPE_REQUIRED'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1
                     FROM unnest(
                         coalesce(requirement_row.preferred_locations, '{}'::text[])
                         || coalesce(requirement_row.acceptable_locations, '{}'::text[])
                     ) AS location(value)
                     WHERE btrim(location.value) <> ''
                 )
                THEN 'LOCATION_REQUIRED'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND (
                     requirement_row.target_rent IS NULL
                     OR requirement_row.max_rent IS NULL
                     OR requirement_row.target_rent <= 0
                     OR requirement_row.max_rent <= 0
                     OR requirement_row.target_rent > requirement_row.max_rent
                 )
                THEN 'INVALID_RENT_RANGE'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND requirement_row.preferred_move_in_date IS NULL
                 AND requirement_row.latest_move_in_date IS NULL
                THEN 'MOVE_IN_DATE_REQUIRED'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND requirement_row.preferred_move_in_date IS NOT NULL
                 AND requirement_row.latest_move_in_date IS NOT NULL
                 AND requirement_row.preferred_move_in_date
                     > requirement_row.latest_move_in_date
                THEN 'INVALID_MOVE_IN_WINDOW'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND EXISTS (
                     SELECT 1
                     FROM unnest(
                         coalesce(
                             requirement_row.preferred_property_configurations,
                             '{}'::text[]
                         )
                     ) AS configuration(value)
                     WHERE configuration.value IS NULL
                        OR upper(replace(btrim(configuration.value), ' ', ''))
                           NOT IN (
                               '1RK', '1BHK', '2BHK', '3BHK',
                               '4BHK', '4+BHK'
                           )
                 )
                THEN 'INVALID_PROPERTY_CONFIGURATION'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND 'ENTIRE_PROPERTY'::public.listing_type = ANY(
                     coalesce(
                         requirement_row.listing_types,
                         '{}'::public.listing_type[]
                     )
                 )
                 AND coalesce(
                     cardinality(
                         requirement_row.preferred_property_configurations
                     ),
                     0
                 ) = 0
                 AND coalesce(
                     requirement_row.additional_preferences
                         ->> '__flathunter_configuration_answered',
                     'false'
                 ) <> 'true'
                THEN 'ENTIRE_PROPERTY_CONFIGURATION_REQUIRED'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND (
                     requirement_row.core_preferences IS NULL
                     OR jsonb_typeof(requirement_row.core_preferences) <> 'object'
                 )
                THEN 'INVALID_CORE_PREFERENCES'
            END,
            CASE
                WHEN requirement_row.search_id IS NOT NULL
                 AND (
                     requirement_row.additional_preferences IS NULL
                     OR jsonb_typeof(requirement_row.additional_preferences) <> 'object'
                 )
                THEN 'INVALID_ADDITIONAL_PREFERENCES'
            END
        ], NULL) AS issues
    FROM public.search_sessions AS session_row
    LEFT JOIN public.search_requirements AS requirement_row
      ON requirement_row.search_id = session_row.id
    WHERE session_row.status = 'DRAFT'
)
SELECT
    draft_issues.*,
    NOT EXISTS (
        SELECT 1 FROM public.renter_availability
        WHERE search_id = draft_issues.search_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM public.matches
        WHERE search_id = draft_issues.search_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM public.conversations
        WHERE search_id = draft_issues.search_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM public.visits
        WHERE search_id = draft_issues.search_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM public.agent_jobs
        WHERE payload ->> 'search_id' = draft_issues.search_id::text
    ) AS eligible_for_optional_cleanup
FROM draft_issues
WHERE cardinality(issues) > 0
ORDER BY created_at, search_id;
```

For an ENTIRE_PROPERTY draft, either preferred_property_configurations must contain at least one value or additional_preferences.__flathunter_configuration_answered must be the string true, which records that the renter explicitly selected **Any configuration**. The audit and cleanup guard use the same rule as the lifecycle validation RPC.

This separate read-only query checks for requirement rows whose parent session is missing. The foreign key should make this impossible, so it should return zero rows.

```sql
SELECT requirement_row.id, requirement_row.search_id
FROM public.search_requirements AS requirement_row
LEFT JOIN public.search_sessions AS session_row
  ON session_row.id = requirement_row.search_id
WHERE session_row.id IS NULL
ORDER BY requirement_row.created_at, requirement_row.id;
```

## 5. Optional guarded cleanup

This is **not part of migration 004**. Use it only after reviewing/exporting the audit results. It permanently deletes only invalid `DRAFT` sessions that have no availability, match, conversation, visit, or agent-job reference. It never targets `ACTIVE`, `PAUSED`, `CLOSED`, or valid `DRAFT` sessions.

The block is intentionally disabled. To authorize one cleanup run, change only:

```sql
confirmation CONSTANT text := 'KEEP_DATA';
```

to:

```sql
confirmation CONSTANT text := 'DELETE_FLATHUNTER';
```

Then run the complete block once. Leaving `KEEP_DATA` in place raises an exception before any delete occurs.

```sql
DO $cleanup$
DECLARE
    confirmation CONSTANT text := 'KEEP_DATA';
    candidate RECORD;
    deleted_count integer := 0;
BEGIN
    IF confirmation IS DISTINCT FROM 'DELETE_FLATHUNTER' THEN
        RAISE EXCEPTION
            'Cleanup not authorized. Set confirmation to DELETE_FLATHUNTER deliberately.';
    END IF;

    FOR candidate IN
        SELECT session_row.id
        FROM public.search_sessions AS session_row
        LEFT JOIN public.search_requirements AS requirement_row
          ON requirement_row.search_id = session_row.id
        WHERE session_row.status = 'DRAFT'
          AND (
              session_row.user_id IS NULL
              OR btrim(session_row.city) = ''
              OR requirement_row.search_id IS NULL
              OR NOT EXISTS (
                  SELECT 1
                  FROM unnest(
                      coalesce(
                          requirement_row.listing_types,
                          '{}'::public.listing_type[]
                      )
                  ) AS listing_kind(value)
                  WHERE listing_kind.value IS NOT NULL
              )
              OR NOT EXISTS (
                  SELECT 1
                  FROM unnest(
                      coalesce(requirement_row.preferred_locations, '{}'::text[])
                      || coalesce(requirement_row.acceptable_locations, '{}'::text[])
                  ) AS location(value)
                  WHERE btrim(location.value) <> ''
              )
              OR requirement_row.target_rent IS NULL
              OR requirement_row.max_rent IS NULL
              OR requirement_row.target_rent <= 0
              OR requirement_row.max_rent <= 0
              OR requirement_row.target_rent > requirement_row.max_rent
              OR (
                  requirement_row.preferred_move_in_date IS NULL
                  AND requirement_row.latest_move_in_date IS NULL
              )
              OR (
                  requirement_row.preferred_move_in_date IS NOT NULL
                  AND requirement_row.latest_move_in_date IS NOT NULL
                  AND requirement_row.preferred_move_in_date
                      > requirement_row.latest_move_in_date
              )
              OR (
                  EXISTS (
                      SELECT 1
                      FROM unnest(
                          coalesce(
                              requirement_row.preferred_property_configurations,
                              '{}'::text[]
                          )
                      ) AS configuration(value)
                      WHERE configuration.value IS NULL
                         OR upper(replace(btrim(configuration.value), ' ', ''))
                            NOT IN (
                                '1RK', '1BHK', '2BHK', '3BHK',
                                '4BHK', '4+BHK'
                            )
                  )
              )
              OR (
                  'ENTIRE_PROPERTY'::public.listing_type = ANY(
                      coalesce(
                          requirement_row.listing_types,
                          '{}'::public.listing_type[]
                      )
                  )
                  AND coalesce(
                      cardinality(
                          requirement_row.preferred_property_configurations
                      ),
                      0
                  ) = 0
                  AND coalesce(
                      requirement_row.additional_preferences
                          ->> '__flathunter_configuration_answered',
                      'false'
                  ) <> 'true'
              )
              OR requirement_row.core_preferences IS NULL
              OR jsonb_typeof(requirement_row.core_preferences) <> 'object'
              OR requirement_row.additional_preferences IS NULL
              OR jsonb_typeof(requirement_row.additional_preferences) <> 'object'
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.renter_availability
              WHERE search_id = session_row.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.matches
              WHERE search_id = session_row.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.conversations
              WHERE search_id = session_row.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.visits
              WHERE search_id = session_row.id
          )
          AND NOT EXISTS (
              SELECT 1 FROM public.agent_jobs
              WHERE payload ->> 'search_id' = session_row.id::text
          )
        ORDER BY session_row.created_at, session_row.id
        FOR UPDATE OF session_row
    LOOP
        DELETE FROM public.search_requirements
        WHERE search_id = candidate.id;

        DELETE FROM public.search_sessions
        WHERE id = candidate.id
          AND status = 'DRAFT';

        deleted_count := deleted_count + 1;
    END LOOP;

    RAISE NOTICE 'Deleted % invalid, dependency-free DRAFT search session(s).',
        deleted_count;
END;
$cleanup$;
```

After an authorized cleanup, rerun the two read-only audit queries. Investigate any remaining rows manually; they were intentionally preserved because they have downstream references or require a business decision.
