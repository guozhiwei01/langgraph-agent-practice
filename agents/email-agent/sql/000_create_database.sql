\set ON_ERROR_STOP on

-- Run this file while connected to the postgres maintenance database:
-- psql -U postgres -d postgres -f sql/000_create_database.sql
--
-- \gexec executes CREATE DATABASE only when the database does not exist.
SELECT 'CREATE DATABASE email_agent WITH ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = 'email_agent'
) \gexec

