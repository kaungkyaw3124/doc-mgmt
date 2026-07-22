-- Runs automatically on first Postgres container startup (mounted into
-- /docker-entrypoint-initdb.d/). Creates a second database so catalogue-service
-- has its own isolated schema/migration history, while still sharing one
-- Postgres server process with document-service.

CREATE DATABASE catalogue OWNER docmgmt;
