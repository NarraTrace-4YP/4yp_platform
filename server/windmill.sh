#!/usr/bin/env bash
# Sets Windmill up for the project, through its API. Safe to run again: anything already
# done is left alone, and settings that changed in .env are updated.
#
#   sudo server/windmill.sh configure    run by setup.sh and update.sh
#
# What "configure" does:
#   1. replaces Windmill's built-in admin@windmill.dev login (password "changeme") with
#      an admin account whose password is WINDMILL_ADMIN_PASSWORD in .env,
#   2. tells Windmill its web address,
#   3. creates the "dhr" workspace and its "dhr" folder, which everyone in the team can
#      see, run and edit. Edits made in Windmill are brought back to GitHub as a pull
#      request (.github/workflows/windmill-to-github.yml),
#   4. stores the passwords and tokens the pipelines need as secret Windmill variables
#      (f/dhr/...), so they never appear in code,
#   5. creates a "deploy" account, and a token for it that GitHub uses to push code.
#
# team.sh also uses the functions below to create and remove people's Windmill logins.
set -euo pipefail

WM_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WM_ENV_FILE="$WM_REPO_DIR/.env"
WM_API="http://127.0.0.1:8000/api"
WM_WORKSPACE="dhr"
WM_TOKEN="${WM_TOKEN:-}"

wm_die() {
	printf '\nERROR: %s\n' "$*" >&2
	exit 1
}

wm_set_env() { # name value: write a setting into .env
	local value=${2//\\/\\\\}
	value=${value//&/\\&}
	value=${value//|/\\|}
	if grep -qE "^$1=" "$WM_ENV_FILE"; then
		sed -i "s|^$1=.*|$1=$value|" "$WM_ENV_FILE"
	else
		printf '%s=%s\n' "$1" "$2" >>"$WM_ENV_FILE"
	fi
}

wm_call() { # method path [json] -> prints the response; returns 1 on an HTTP error
	local method=$1 path=$2 body=${3:-} response status
	local args=(-sS -X "$method" -w '\n%{http_code}' --max-time 60)
	[[ -n "$WM_TOKEN" ]] && args+=(-H "Authorization: Bearer $WM_TOKEN")
	[[ -n "$body" ]] && args+=(-H "Content-Type: application/json" --data "$body")
	response=$(curl "${args[@]}" "$WM_API$path") || return 1
	status=${response##*$'\n'}
	response=${response%$'\n'*}
	if ((status >= 400)); then
		printf '%s' "$response" >&2
		return 1
	fi
	printf '%s' "$response"
}

wm_api() { # like wm_call, but stops the script with a clear message on an error
	local result
	if ! result=$(wm_call "$@" 2>/tmp/wm_error); then
		wm_die "Windmill refused $1 $2: $(cat /tmp/wm_error)"
	fi
	printf '%s' "$result"
}

wm_wait() { # until the Windmill server answers (up to 5 minutes)
	for _ in $(seq 1 60); do
		curl -fsS --max-time 5 "$WM_API/version" >/dev/null 2>&1 && return 0
		sleep 5
	done
	wm_die "Windmill isn't answering on 127.0.0.1:8000. See: docker compose logs windmill-server"
}

wm_login() { # email password -> prints a session token
	local saved=$WM_TOKEN token
	WM_TOKEN=""
	token=$(wm_call POST /auth/login "$(jq -n --arg e "$1" --arg p "$2" '{email: $e, password: $p}')" 2>/dev/null) || token=""
	WM_TOKEN=$saved
	[[ -n "$token" ]] && printf '%s' "$token"
}

wm_admin_token() { # makes sure WM_TOKEN is the server-scripts admin token
	local admin_email="admin@$DOMAIN" session
	WM_TOKEN="${WINDMILL_ADMIN_TOKEN:-}"
	if [[ -n "$WM_TOKEN" ]] && wm_call GET /users/whoami >/dev/null 2>&1; then
		return 0
	fi

	echo "  setting up Windmill's admin account ($admin_email)"
	if ! session=$(wm_login "$admin_email" "$WINDMILL_ADMIN_PASSWORD"); then
		# First run: use the built-in admin to create ours.
		session=$(wm_login admin@windmill.dev changeme) ||
			wm_die "Can't log in to Windmill as $admin_email or as the built-in admin@windmill.dev."
		WM_TOKEN=$session
		wm_api POST /users/create "$(jq -n --arg e "$admin_email" --arg p "$WINDMILL_ADMIN_PASSWORD" \
			'{email: $e, password: $p, super_admin: true, name: "Server admin", skip_email: true}')" >/dev/null
		session=$(wm_login "$admin_email" "$WINDMILL_ADMIN_PASSWORD") ||
			wm_die "Created $admin_email in Windmill but can't log in with it."
	fi
	WM_TOKEN=$session
	WM_TOKEN=$(wm_api POST /users/tokens/create '{"label": "server scripts (server/windmill.sh)"}')
	wm_set_env WINDMILL_ADMIN_TOKEN "$WM_TOKEN"
	WINDMILL_ADMIN_TOKEN=$WM_TOKEN

	# The built-in admin has a well-known password, so it goes.
	if wm_call DELETE /users/delete/admin@windmill.dev >/dev/null 2>&1; then
		echo "  removed the built-in admin@windmill.dev login"
	fi
}

wm_exists() { # path -> 0 if the API answers "true" for it
	[[ "$(wm_api "$@")" == "true" ]]
}

# Windmill names people itself, from the part of their email before the @ (adding a number
# if that name is taken), and refuses names given to it. This looks up the name it chose.
wm_member_username() { # email -> their username in the workspace (empty if not a member)
	wm_api GET "/w/$WM_WORKSPACE/users/list" | jq -r --arg e "$1" '.[] | select(.email == $e) | .username'
}

wm_variable() { # path value is_secret description
	local body
	body=$(jq -n --arg path "$1" --arg value "$2" --argjson secret "$3" --arg d "$4" \
		'{path: $path, value: $value, is_secret: $secret, description: $d}')
	if wm_exists GET "/w/$WM_WORKSPACE/variables/exists/$1"; then
		wm_api POST "/w/$WM_WORKSPACE/variables/update/$1" "$body" >/dev/null
	else
		wm_api POST "/w/$WM_WORKSPACE/variables/create" "$body" >/dev/null
	fi
}

wm_objective_embed() { # app-path config-name public-origin
	local app_path=$1 name=$2 public_origin=$3 secret public_app runtime_dir
	if ! wm_exists GET "/w/$WM_WORKSPACE/apps/exists/$app_path"; then
		echo "  $app_path is not deployed yet; run server/update.sh after its first GitHub deploy"
		return 0
	fi

	# Community Edition does not route public custom paths. Its app-level share ID is
	# stable across app versions but intentionally differs between Windmill instances.
	wm_api POST "/w/$WM_WORKSPACE/apps/update/$app_path" \
		'{"execution_mode":"anonymous","skip_draft_deletion":true}' >/dev/null
	secret=$(wm_api GET "/w/$WM_WORKSPACE/apps/secret_of/$app_path")
	[[ "$secret" =~ ^[0-9a-f]{32}$ ]] || wm_die "Windmill returned an invalid share ID for $app_path."
	public_app=$(curl -fsS --max-time 20 \
		"$WM_API/w/$WM_WORKSPACE/apps_u/public_app/$secret") ||
		wm_die "Windmill did not expose the public share for $app_path."
	[[ "$(jq -r .path <<<"$public_app")" == "$app_path" ]] ||
		wm_die "The public share for $app_path points at a different app."

	runtime_dir="$WM_REPO_DIR/runtime"
	mkdir -p "$runtime_dir"
	jq -n --arg url "$public_origin/public/$WM_WORKSPACE/$secret" '{url: $url}' \
		>"$runtime_dir/$name.json.tmp"
	mv "$runtime_dir/$name.json.tmp" "$runtime_dir/$name.json"
	chmod 644 "$runtime_dir/$name.json"
	echo "  published the $name landing-page embed"
}

# ---------------------------------------------------------------- people (used by team.sh)

wm_user_email() { echo "$1@$DOMAIN"; } # windmill username -> login email

wm_set_user() { # username password is-admin(yes/no) github-name
	local email admin=false
	email=$(wm_user_email "$1")
	[[ "$3" == yes ]] && admin=true
	if ! wm_call POST /users/create "$(jq -n --arg e "$email" --arg p "$2" --argjson a "$admin" --arg n "$4" \
		'{email: $e, password: $p, super_admin: $a, name: $n, skip_email: true}')" >/dev/null 2>&1; then
		# Already has a login: give it the new password and the right admin rights.
		wm_api POST "/users/set_password_of/$email" "$(jq -n --arg p "$2" '{password: $p}')" >/dev/null
		wm_api POST "/users/update/$email" "$(jq -n --argjson a "$admin" '{is_super_admin: $a, disabled: false}')" >/dev/null
	fi
	if [[ -z "$(wm_member_username "$email")" ]]; then
		wm_api POST "/w/$WM_WORKSPACE/workspaces/add_user" \
			"$(jq -n --arg e "$email" --argjson a "$admin" '{email: $e, is_admin: $a}')" >/dev/null
	fi
}

wm_disable_user() { # username
	wm_call POST "/users/update/$(wm_user_email "$1")" '{"disabled": true}' >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------- configure

wm_configure() {
	local public_origin="${PUBLIC_SCHEME:-https}://windmill.$DOMAIN"
	echo "  waiting for Windmill to answer..."
	wm_wait
	wm_admin_token

	wm_api POST /settings/global/base_url "$(jq -n --arg v "$public_origin" '{value: $v}')" >/dev/null
	echo "  web address: $public_origin"

	if ! wm_exists POST /workspaces/exists "{\"id\": \"$WM_WORKSPACE\"}"; then
		wm_api POST /workspaces/create "{\"id\": \"$WM_WORKSPACE\", \"name\": \"DHR project\"}" >/dev/null
		echo "  created the workspace \"$WM_WORKSPACE\""
	fi
	local admin_username
	admin_username=$(wm_api GET "/w/$WM_WORKSPACE/users/whoami" | jq -r .username)
	[[ -n "$admin_username" && "$admin_username" != null ]] || wm_die "Can't find the admin account's name in the $WM_WORKSPACE workspace."

	# The "deploy" account pushes the pipelines from GitHub. It's an admin of this
	# workspace only, not of the whole of Windmill.
	local deploy_email="deploy@$DOMAIN"
	if wm_call POST /users/create "$(jq -n --arg e "$deploy_email" --arg p "$(openssl rand -hex 24)" \
		'{email: $e, password: $p, super_admin: false, name: "GitHub deploys", skip_email: true}')" >/dev/null 2>&1; then
		echo "  created the deploy account ($deploy_email)"
	fi
	if [[ -z "$(wm_member_username "$deploy_email")" ]]; then
		wm_api POST "/w/$WM_WORKSPACE/workspaces/add_user" \
			"$(jq -n --arg e "$deploy_email" '{email: $e, is_admin: true}')" >/dev/null
	fi

	# f/dhr holds the team's pipelines. Everyone can see, run and edit them ("g/all": true
	# means read and write). Edits made here reach GitHub as a pull request within about
	# 15 minutes, and deploys from GitHub stop rather than overwrite edits not there yet.
	local folder
	# (Windmill insists that every owner can also write.)
	folder=$(jq -n --arg owner "u/$admin_username" '{summary: "The team pipelines, kept in GitHub (NarraTrace-4YP/4yp_platform). Edits saved here run straight away and reach GitHub as a pull request within about 15 minutes.",
		owners: [$owner], extra_perms: {($owner): true, "g/all": true}}')
	if wm_exists GET "/w/$WM_WORKSPACE/folders/exists/dhr"; then
		wm_api POST "/w/$WM_WORKSPACE/folders/update/dhr" "$folder" >/dev/null
	else
		wm_api POST "/w/$WM_WORKSPACE/folders/create" "$(jq '. + {name: "dhr", display_name: "dhr"}' <<<"$folder")" >/dev/null
		echo "  created the folder f/dhr"
	fi

	wm_variable f/dhr/database_url "postgresql://pipeline:${PIPELINE_DB_PASSWORD}@postgres:5432/dhr" true \
		"Login the pipelines use for the project database"
	wm_variable f/dhr/hf_token "${HF_TOKEN:-not-set}" true \
		"Hugging Face token that can read DHRC-100 (set HF_TOKEN in /opt/dhr/.env, then run update.sh)"
	wm_variable f/dhr/mlflow_tracking_uri "http://mlflow:5000" false "Where the pipelines log runs"
	wm_variable f/dhr/mlflow_username "admin" false "MLflow login the pipelines use"
	wm_variable f/dhr/mlflow_password "$MLFLOW_ADMIN_PASSWORD" true "MLflow password the pipelines use"
	echo "  stored the pipelines' settings as secret variables in f/dhr"

	wm_objective_embed f/dhr/O1/app o1 "$public_origin"

	# The token GitHub Actions uses to deploy. Made once; kept in .env.
	local saved_token=$WM_TOKEN
	if [[ -n "${WINDMILL_DEPLOY_TOKEN:-}" ]]; then
		WM_TOKEN=$WINDMILL_DEPLOY_TOKEN
		wm_call GET /users/whoami >/dev/null 2>&1 || WINDMILL_DEPLOY_TOKEN=""
		WM_TOKEN=$saved_token
	fi
	if [[ -z "${WINDMILL_DEPLOY_TOKEN:-}" ]]; then
		WINDMILL_DEPLOY_TOKEN=$(wm_api POST /users/tokens/impersonate "$(jq -n --arg e "$deploy_email" \
			--arg w "$WM_WORKSPACE" '{impersonate_email: $e, label: "GitHub Actions deploys", workspace_id: $w}')")
		wm_set_env WINDMILL_DEPLOY_TOKEN "$WINDMILL_DEPLOY_TOKEN"
		echo "  created the GitHub deploy token (WINDMILL_DEPLOY_TOKEN in .env)"
	fi
}

wm_load_env() {
	[[ -f "$WM_ENV_FILE" ]] || wm_die "No .env file in $WM_REPO_DIR. Run server/setup.sh first."
	set -a
	# shellcheck source=/dev/null
	. "$WM_ENV_FILE"
	set +a
	command -v jq >/dev/null || wm_die "jq isn't installed. Run: sudo apt-get install -y jq"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	[[ $EUID -eq 0 ]] || wm_die "Run this with sudo."
	case "${1:-}" in
	configure)
		wm_load_env
		wm_configure
		;;
	*)
		echo "Usage: $0 configure" >&2
		exit 1
		;;
	esac
fi
