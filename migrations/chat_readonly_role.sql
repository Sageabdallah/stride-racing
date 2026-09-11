-- chat_readonly_role.sql: the role the STRIDE chat connects with.
--
-- Why a role and not DATABASE_URL: the chat is the one component that faces
-- untrusted input, and DATABASE_URL is the owner role. A prompt injection
-- that reaches a query must find nothing to write with. This role can read
-- every table (pg_read_all_data, which covers tables created later by the
-- app's runtime DDL, such as blackbook_entries) and can do nothing else.
--
-- The password is NOT in this file. The apply-migration workflow replaces the
-- token __STRIDE_CHAT_RO_PASSWORD__ with the repository secret of the same
-- name at execution time, quoted by the driver; the SQL it prints before
-- applying shows the token, never the value. Re-running the migration with a
-- new secret rotates the password, which is the intended rotation path.
--
-- Two settings that are convenience, not security: default_transaction_read_only
-- and the timeouts are session defaults a client can override. The boundary is
-- the absence of write privilege, and server/python/chat/verify_readonly_role.py
-- proves that boundary in the same workflow run, inside an explicitly READ WRITE
-- transaction, so the default cannot stand in for the grant.
--
-- Idempotent: safe to re-run. Requires the applying role to be able to grant
-- pg_read_all_data (Neon's owner role can; on a plain PostgreSQL it needs
-- ADMIN OPTION on that role or superuser). If the GRANT is refused, the
-- fallback is GRANT SELECT ON ALL TABLES IN SCHEMA public plus ALTER DEFAULT
-- PRIVILEGES for future tables, applied by hand and recorded here.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stride_chat_ro') THEN
        CREATE ROLE stride_chat_ro;
    END IF;
END
$$;

ALTER ROLE stride_chat_ro WITH
    LOGIN
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    NOBYPASSRLS
    INHERIT
    CONNECTION LIMIT 10
    PASSWORD __STRIDE_CHAT_RO_PASSWORD__;

GRANT pg_read_all_data TO stride_chat_ro;

-- Session defaults for an interactive caller (run_tips_pipeline.db_connect
-- uses the same 15 s statement timeout). The chat's own connection sets the
-- session read-only as well; both are belts over the privilege boundary.
ALTER ROLE stride_chat_ro SET default_transaction_read_only = on;
ALTER ROLE stride_chat_ro SET statement_timeout = '15s';
ALTER ROLE stride_chat_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE stride_chat_ro SET lock_timeout = '2s';

-- No object creation. This revokes any direct grant; the verifier's
-- CREATE TABLE check fails loudly if PUBLIC still confers CREATE on the
-- schema (PostgreSQL 14 and earlier defaults), in which case the operator
-- decides on REVOKE CREATE ON SCHEMA public FROM PUBLIC, which affects
-- every role and is therefore not done here.
REVOKE CREATE ON SCHEMA public FROM stride_chat_ro;

COMMENT ON ROLE stride_chat_ro IS
    'STRIDE chat: read-only. pg_read_all_data and nothing else. Created by migrations/chat_readonly_role.sql; proven by server/python/chat/verify_readonly_role.py.';
