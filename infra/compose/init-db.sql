-- ---------------------------------------------------------------------------
-- Postgres init script — mounted into the official postgres image at
-- /docker-entrypoint-initdb.d/ on first container boot only.
--
-- Adds the extensions the OpenAgentDojo schema depends on (see
-- IMPLEMENTATION_PLAN.md §6): citext for case-insensitive email columns,
-- pgcrypto for gen_random_uuid().
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
