#!/usr/bin/env bash
# WeKnora's server settings and logins (docs/weknora.md).
#
#   sudo server/weknora.sh env    run by setup.sh and update.sh before the services start
#
# "env" fills in WeKnora's secrets in .env if they are missing (an existing .env from before
# WeKnora was added has none), and names its system admin: the first admin in team.txt.
# Secrets already there are never changed. WEKNORA_AES_KEY in particular encrypts the API
# keys people save in WeKnora, so changing it would make every saved key unreadable.
#
# team.sh uses the functions below to create and lock people's WeKnora logins. A login is
# made through WeKnora's own sign-up API from inside the server, which the Caddyfile closes
# to the internet, so nobody else can make one.
set -euo pipefail

WK_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WK_ENV_FILE="$WK_REPO_DIR/.env"

wk_die() {
	printf '\nERROR: %s\n' "$*" >&2
	exit 1
}

wk_compose() { docker compose --project-directory "$WK_REPO_DIR" "$@"; }

wk_env_value() { # name -> its value in .env (empty if missing)
	grep -E "^$1=" "$WK_ENV_FILE" | head -1 | cut -d= -f2- || true
}

wk_set_env() { # name value: write a setting into .env
	local value=${2//\\/\\\\}
	value=${value//&/\\&}
	value=${value//|/\\|}
	if grep -qE "^$1=" "$WK_ENV_FILE"; then
		sed -i "s|^$1=.*|$1=$value|" "$WK_ENV_FILE"
	else
		printf '%s=%s\n' "$1" "$2" >>"$WK_ENV_FILE"
	fi
}

# A person's WeKnora login email: the same as their Windmill one (windmill.sh wm_user_email).
# GitHub names are lower-cased, and hyphens become underscores as in Windmill.
wk_user_email() { # linux-username -> email
	echo "$(tr '-' '_' <<<"$1")@$(wk_env_value DOMAIN)"
}

wk_env() {
	[[ -f "$WK_ENV_FILE" ]] || wk_die "No .env file in $WK_REPO_DIR. Run server/setup.sh first."
	local name bytes
	# name and the number of random bytes. WeKnora needs the AES key to be exactly
	# 32 characters, which is 16 bytes written as hex.
	for name in WEKNORA_DB_PASSWORD:24 WEKNORA_REDIS_PASSWORD:24 WEKNORA_JWT_SECRET:32 \
		WEKNORA_AES_KEY:16 WEKNORA_SIGNING_KEY:32 WEKNORA_GRAPH_PASSWORD:24; do
		bytes=${name#*:}
		name=${name%%:*}
		if [[ -z "$(wk_env_value "$name")" ]]; then
			wk_set_env "$name" "$(openssl rand -hex "$bytes")"
			echo "  generated $name"
		fi
	done

	if [[ -z "$(wk_env_value WEKNORA_ADMIN_EMAIL)" ]]; then
		local gh role admin=""
		while read -r gh role _; do
			[[ -z "${gh:-}" || "$gh" == \#* ]] && continue
			if [[ "${role:-}" == admin ]]; then
				admin=$(tr '[:upper:]' '[:lower:]' <<<"$gh")
				break
			fi
		done <"$WK_REPO_DIR/server/team.txt"
		if [[ -n "$admin" && -n "$(wk_env_value DOMAIN)" ]]; then
			wk_set_env WEKNORA_ADMIN_EMAIL "$(wk_user_email "$admin")"
			echo "  WeKnora's system admin will be $admin ($(wk_user_email "$admin"))"
		fi
	fi
}

wk_running() {
	[[ "$(docker inspect -f '{{.State.Health.Status}}' "$(wk_compose ps -q weknora-app 2>/dev/null)" 2>/dev/null)" == healthy ]]
}

# WeKnora's passwords must be 8-32 characters with at least one letter and one number.
# The shared passwords team.sh makes are 24 characters, but a rare one has no number (or no
# letter), so this adds one where needed. Prints the password to use.
wk_password() { # shared-password
	local password=$1
	[[ "$password" =~ [0-9] ]] || password="${password}7"
	[[ "$password" =~ [A-Za-z] ]] || password="${password}k"
	echo "$password"
}

wk_create_login() { # linux-username password: prints "created", "exists" or the error
	local body response
	body=$(jq -n --arg u "$1" --arg e "$(wk_user_email "$1")" --arg p "$2" \
		'{username: $u, email: $e, password: $p}')
	response=$(wk_compose exec -T weknora-app curl -sS -X POST \
		-H 'Content-Type: application/json' --data-binary @- \
		http://localhost:8080/api/v1/auth/register <<<"$body" 2>&1) || true
	if jq -e '.success == true' <<<"$response" >/dev/null 2>&1; then
		echo created
	elif grep -qi 'already exists' <<<"$response"; then
		echo exists
	else
		echo "$response"
	fi
}

# Locks a login straight in WeKnora's database: its admin API needs an admin's password,
# which the server doesn't keep. Someone already logged in keeps access until their login
# session runs out.
wk_disable_login() { # linux-username
	wk_compose exec -T postgres psql -q -U postgres -d weknora -v e="$(wk_user_email "$1")" <<-'SQL' || true
		UPDATE users SET is_active = false WHERE lower(email) = lower(:'e');
		SQL
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	[[ $EUID -eq 0 ]] || wk_die "Run this with sudo."
	case "${1:-}" in
	env) wk_env ;;
	*)
		echo "Usage: $0 env" >&2
		exit 1
		;;
	esac
fi
