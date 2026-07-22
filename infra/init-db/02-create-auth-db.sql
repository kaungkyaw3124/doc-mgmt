-- Runs automatically on first Postgres container startup. Creates a third
-- database so auth-service has its own isolated schema, same pattern used
-- for catalogue-service's database.

CREATE DATABASE auth OWNER docmgmt;
