#!/usr/bin/env bash
# Provisions and imports this checkout into local Windmill. There is deliberately no
# GitHub account, token, branch, pull-request or synchronization behavior in this script.
set -euo pipefail

api="${WINDMILL_INTERNAL_URL:?WINDMILL_INTERNAL_URL is required}/api"
workspace=dhr
token=""
project_dir=$(mktemp -d)
trap 'rm -rf "$project_dir"' EXIT
cp /source/wmill.yaml "$project_dir/wmill.yaml"
cp -R /source/f "$project_dir/f"
cd "$project_dir"

die() {
	printf '\nERROR: %s\n' "$*" >&2
	exit 1
}

call() { # method path [json]
	local method=$1 path=$2 body=${3:-} response status
	local args=(-sS -X "$method" -w $'\n%{http_code}' --max-time 60)
	[[ -n "$token" ]] && args+=(-H "Authorization: Bearer $token")
	[[ -n "$body" ]] && args+=(-H "Content-Type: application/json" --data "$body")
	response=$(curl "${args[@]}" "$api$path") || return 1
	status=${response##*$'\n'}
	response=${response%$'\n'*}
	if ((status >= 400)); then
		printf '%s' "$response" >&2
		return 1
	fi
	printf '%s' "$response"
}

login() { # email password
	local saved=$token result
	token=""
	result=$(call POST /auth/login "$(jq -n --arg e "$1" --arg p "$2" '{email: $e, password: $p}')" 2>/dev/null) || result=""
	token=$saved
	[[ -n "$result" ]] && printf '%s' "$result"
}

exists() { [[ "$(call "$@")" == true ]]; }

variable() { # path value is-secret description
	local body
	body=$(jq -n --arg path "$1" --arg value "$2" --argjson secret "$3" --arg d "$4" \
		'{path: $path, value: $value, is_secret: $secret, description: $d}')
	if exists GET "/w/$workspace/variables/exists/$1"; then
		call POST "/w/$workspace/variables/update/$1" "$body" >/dev/null
	else
		call POST "/w/$workspace/variables/create" "$body" >/dev/null
	fi
}

printf 'Waiting for local Windmill...\n'
for _ in $(seq 1 60); do
	curl -fsS --max-time 5 "$api/version" >/dev/null 2>&1 && break
	sleep 2
done
curl -fsS --max-time 5 "$api/version" >/dev/null || die "Local Windmill did not become ready."

admin_email=admin@local.invalid
if ! session=$(login "$admin_email" "$WINDMILL_ADMIN_PASSWORD"); then
	# admin@localhost was briefly used by the first local bootstrap implementation; accept
	# it once so affected local volumes migrate without being deleted. The Windmill built-in
	# account exists only on a brand-new local volume.
	if ! session=$(login admin@localhost "$WINDMILL_ADMIN_PASSWORD"); then
		session=$(login admin@windmill.dev changeme) || die \
			"Cannot log in. WINDMILL_ADMIN_PASSWORD in .env.local must match the local admin."
	fi
	token=$session
	call POST /users/create "$(jq -n --arg e "$admin_email" --arg p "$WINDMILL_ADMIN_PASSWORD" \
		'{email: $e, password: $p, super_admin: true, name: "Local admin", skip_email: true}')" >/dev/null
	session=$(login "$admin_email" "$WINDMILL_ADMIN_PASSWORD") || die "Created $admin_email but could not log in."
fi
token=$session
call DELETE /users/delete/admin@windmill.dev >/dev/null 2>&1 || true
call DELETE /users/delete/admin@localhost >/dev/null 2>&1 || true

call POST /settings/global/base_url "$(jq -n --arg v "$WINDMILL_PUBLIC_URL" '{value: $v}')" >/dev/null

if ! exists POST /workspaces/exists "{\"id\":\"$workspace\"}"; then
	call POST /workspaces/create "{\"id\":\"$workspace\",\"name\":\"DHR project (local)\"}" >/dev/null
	printf 'Created workspace %s.\n' "$workspace"
fi

admin_username=$(call GET "/w/$workspace/users/whoami" | jq -r .username)
[[ -n "$admin_username" && "$admin_username" != null ]] || die "Cannot find the local admin in workspace $workspace."
folder=$(jq -n --arg owner "u/$admin_username" '{
	summary: "Local copy from this checkout. Local edits stay local and may be overwritten by the next bootstrap.",
	owners: [$owner], extra_perms: {($owner): true, "g/all": true}
}')
if exists GET "/w/$workspace/folders/exists/dhr"; then
	call POST "/w/$workspace/folders/update/dhr" "$folder" >/dev/null
else
	call POST "/w/$workspace/folders/create" "$(jq '. + {name:"dhr", display_name:"dhr"}' <<<"$folder")" >/dev/null
fi

variable f/dhr/database_url "postgresql://pipeline:${PIPELINE_DB_PASSWORD}@postgres:5432/dhr" true \
	"Local project database login"
variable f/dhr/hf_token "${HF_TOKEN:-not-set}" true \
	"Local read-only Hugging Face token"
variable f/dhr/mlflow_tracking_uri "http://mlflow:5000" false \
	"Local MLflow service"
variable f/dhr/mlflow_username "admin" false "Local MLflow administrator"
variable f/dhr/mlflow_password "$MLFLOW_ADMIN_PASSWORD" true "Local MLflow password"

printf 'Generating Windmill metadata and importing f/dhr...\n'
wmill generate-metadata --yes --workspace "$workspace" --base-url "$WINDMILL_INTERNAL_URL" --token "$token"
wmill sync push --yes --workspace "$workspace" --base-url "$WINDMILL_INTERNAL_URL" --token "$token" \
	--message "Local bootstrap"

# Windmill CLI 1.875 can deploy a raw app without persisting `public: true`.
# Enforce anonymous access without changing the deployed source.
call POST "/w/$workspace/apps/update/f/dhr/o1" \
	'{"execution_mode":"anonymous","skip_draft_deletion":true}' >/dev/null

# Custom public paths are an Enterprise-only Windmill route. Community Edition instead
# provides a stable, instance-specific app share ID. Discover it after deployment and
# publish only its URL to Caddy's ignored runtime directory.
public_secret=$(call GET "/w/$workspace/apps/secret_of/f/dhr/o1") ||
	die "Windmill did not create the public O1 share URL."
[[ "$public_secret" =~ ^[0-9a-f]{32}$ ]] || die "Windmill returned an invalid O1 share ID."
public_url="$WINDMILL_PUBLIC_URL/public/$workspace/$public_secret"
mkdir -p /runtime
jq -n --arg url "$public_url" '{url: $url}' >/runtime/o1.json.tmp
mv /runtime/o1.json.tmp /runtime/o1.json
public_app=$(curl -fsS --max-time 20 \
	"$WINDMILL_INTERNAL_URL/api/w/$workspace/apps_u/public_app/$public_secret") ||
	die "Windmill did not expose the generated public O1 share URL."
[[ "$(jq -r .path <<<"$public_app")" == f/dhr/o1 ]] ||
	die "The generated public O1 share URL points at the wrong app."
printf '  O1 embed: %s\n' "$public_url"

printf '\nLocal Windmill is ready.\n'
printf '  URL:   %s\n' "$WINDMILL_PUBLIC_URL"
printf '  Login: %s\n' "$admin_email"
printf '  Password: WINDMILL_ADMIN_PASSWORD from .env.local\n'
