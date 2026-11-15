#!/bin/sh
# Starts MLflow with logins switched on. The settings come from compose.yaml.
set -eu

# MLflow reads its login settings from a small file; write it from the environment.
cat > /tmp/basic_auth.ini <<EOF
[mlflow]
# Everyone on the team can view and edit every experiment.
default_permission = EDIT
database_uri = ${MLFLOW_AUTH_DB_URI}
authorization_function = mlflow.server.auth:authenticate_request_basic_auth
EOF
export MLFLOW_AUTH_CONFIG_PATH=/tmp/basic_auth.ini

exec mlflow server \
	--app-name basic-auth \
	--host 0.0.0.0 \
	--port 5000 \
	--workers 2 \
	--backend-store-uri "$MLFLOW_BACKEND_URI" \
	--serve-artifacts \
	--artifacts-destination /mlflow/artifacts \
	--allowed-hosts "$MLFLOW_ALLOWED_HOSTS"
