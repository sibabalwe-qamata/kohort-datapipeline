-- Weekly install-cohort D7 session retention.
--
-- Inputs (registered by astro_recon/retention.py):
--   installs(user_id, install_ts)          one row per raw install row, not yet cleaned
--   events(user_id, event_ts, event_type)  one row per raw event row
--   $as_of                                 DATE, bound as a query parameter
--
-- All timestamps are plain TIMESTAMP values holding the UTC wall clock, and the session
-- time zone is set to UTC before this runs, so date_trunc('week', ...) cuts weeks in UTC.

WITH visible_installs AS (
    -- as-of rule: only installs that existed at the end of UTC day $as_of.
    -- Cleaning rule 1: identical rows count once (DISTINCT).
    SELECT DISTINCT
        user_id,
        install_ts
    FROM installs
    WHERE install_ts < CAST($as_of AS DATE) + INTERVAL 1 DAY
),

first_install AS (
    -- Cleaning rule 2: a user with several installs keeps the earliest one.
    -- Organic installs stay in: there is no filter on media_source.
    SELECT
        user_id,
        MIN(install_ts) AS install_ts
    FROM visible_installs
    GROUP BY user_id
),

cohort AS (
    -- One row per user. date_trunc('week', ...) gives the Monday of the ISO week.
    SELECT
        user_id,
        install_ts,
        CAST(date_trunc('week', install_ts) AS DATE) AS install_week
    FROM first_install
),

visible_sessions AS (
    -- as-of rule for events, and only sessions: purchases never count as retention.
    SELECT
        user_id,
        event_ts
    FROM events
    WHERE event_ts < CAST($as_of AS DATE) + INTERVAL 1 DAY
      AND event_type = 'session'
),

retained_users AS (
    -- A user is D7-retained with at least one session on day 7 after install:
    -- install_ts + 7 days <= event_ts < install_ts + 8 days (half-open window).
    -- Events of users with no install, and events before install, never match this join.
    SELECT DISTINCT
        cohort.user_id
    FROM cohort
    INNER JOIN visible_sessions
        ON visible_sessions.user_id = cohort.user_id
    WHERE visible_sessions.event_ts >= cohort.install_ts + INTERVAL 7 DAY
      AND visible_sessions.event_ts <  cohort.install_ts + INTERVAL 8 DAY
)

-- LEFT JOIN keeps every cohort user, so a week with no retained users shows 0, not NULL.
-- COUNT skips NULLs, so users with no match in retained_users are not counted as retained.
SELECT
    cohort.install_week,
    COUNT(DISTINCT cohort.user_id)                    AS cohort_size,
    COUNT(DISTINCT retained_users.user_id)            AS d7_retained_users,
    ROUND(
        COUNT(DISTINCT retained_users.user_id) * 100.0 / COUNT(DISTINCT cohort.user_id),
        2
    )                                                 AS d7_retention_pct,
    -- Mature: the last install of the week (Sunday, +6 days) has had its full D7 window (+7 more).
    cohort.install_week + 13 <= CAST($as_of AS DATE)  AS is_mature
FROM cohort
LEFT JOIN retained_users
    ON retained_users.user_id = cohort.user_id
GROUP BY cohort.install_week
ORDER BY cohort.install_week;
