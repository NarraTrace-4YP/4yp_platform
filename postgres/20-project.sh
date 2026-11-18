#!/bin/bash
# Runs inside the Linux Postgres container once, after ParadeDB adds its extensions.
# Creates the databases and logins, and the project's three schemas.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
	-v mlflow_pw="$MLFLOW_DB_PASSWORD" \
	-v pipeline_pw="$PIPELINE_DB_PASSWORD" \
	-v wiki_pw="$WIKI_DB_PASSWORD" <<-'SQL'
	-- MLflow's own records, in their own databases.
	CREATE ROLE mlflow LOGIN PASSWORD :'mlflow_pw';
	CREATE DATABASE mlflow OWNER mlflow;
	CREATE DATABASE mlflow_auth OWNER mlflow;

	-- The project database. Everything in it is owned by one shared role, "team",
	-- so anyone on the team can work with anything anyone else creates.
	CREATE ROLE team NOLOGIN;
	ALTER DATABASE dhr OWNER TO team;
	REVOKE ALL ON DATABASE dhr FROM PUBLIC;

	-- The login the pipelines use when they run in Windmill.
	CREATE ROLE pipeline LOGIN PASSWORD :'pipeline_pw' IN ROLE team;
	ALTER ROLE pipeline SET role = 'team';

	-- The login the wiki agent uses. It can read the corpus and nothing else.
	CREATE ROLE wiki_agent LOGIN PASSWORD :'wiki_pw';
	GRANT CONNECT ON DATABASE dhr TO team, wiki_agent;
	SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname dhr <<-'SQL'
	CREATE SCHEMA corpus AUTHORIZATION team;
	CREATE SCHEMA evaluation AUTHORIZATION team;
	CREATE SCHEMA scratch AUTHORIZATION team;
	COMMENT ON SCHEMA corpus IS 'The reviews and everything built from them. The wiki agent can read this.';
	COMMENT ON SCHEMA evaluation IS 'Case metadata and answer keys for evaluation. The wiki agent can never read this.';
	COMMENT ON SCHEMA scratch IS 'Experiments and temporary tables.';

	-- The wiki agent can read every table in corpus, including tables created later.
	GRANT USAGE ON SCHEMA corpus TO wiki_agent;
	ALTER DEFAULT PRIVILEGES FOR ROLE team IN SCHEMA corpus GRANT SELECT ON TABLES TO wiki_agent;
	ALTER ROLE wiki_agent SET search_path = corpus;
	SQL

echo "Project database ready: schemas corpus, evaluation, scratch."
