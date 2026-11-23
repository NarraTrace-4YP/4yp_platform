#!/bin/bash
# Run by the weknora-db-init service on every start (compose.yaml). Creates WeKnora's own
# database and login in the shared Postgres if they are missing, and the extensions WeKnora
# needs, which only the postgres superuser may create. Changes nothing once they exist,
# apart from keeping the login's password in step with WEKNORA_DB_PASSWORD in .env.
# WeKnora's login can reach its own database and nothing else.
set -euo pipefail

if [[ -z "${WEKNORA_DB_PASSWORD:-}" ]]; then
	echo "WEKNORA_DB_PASSWORD is empty. Run server/update.sh, which fills it in .env." >&2
	exit 1
fi

psql -v ON_ERROR_STOP=1 -d postgres -v pw="$WEKNORA_DB_PASSWORD" <<-'SQL'
	SELECT 'CREATE ROLE weknora LOGIN'
	WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'weknora') \gexec
	ALTER ROLE weknora LOGIN PASSWORD :'pw';
	SELECT 'CREATE DATABASE weknora OWNER weknora'
	WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'weknora') \gexec
	REVOKE ALL ON DATABASE weknora FROM PUBLIC;
	GRANT CONNECT, TEMPORARY ON DATABASE weknora TO weknora;
	SQL

psql -v ON_ERROR_STOP=1 -d weknora <<-'SQL'
	ALTER SCHEMA public OWNER TO weknora;
	CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
	CREATE EXTENSION IF NOT EXISTS vector;
	CREATE EXTENSION IF NOT EXISTS pg_trgm;
	CREATE EXTENSION IF NOT EXISTS pg_search;
	-- WeKnora's set-up steps run "ALTER EXTENSION pg_search UPDATE", which only the
	-- extension's owner may run, so its login owns the extensions in its own database.
	-- PostgreSQL has no command for this, hence the direct change. The update changes
	-- nothing here: the extensions are created at the version this image ships.
	UPDATE pg_extension SET extowner = 'weknora'::regrole
	WHERE extname IN ('uuid-ossp', 'vector', 'pg_trgm', 'pg_search');
	SQL

echo "WeKnora database ready."
