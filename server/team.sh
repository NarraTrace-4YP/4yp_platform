#!/usr/bin/env bash
# Manages the team's accounts on the server, based on server/team.txt.
#
#   sudo server/team.sh sync              create accounts for everyone in team.txt
#                                         (safe to run again; it also refreshes SSH keys)
#   sudo server/team.sh remove <github>   lock someone out of everything
#
# Each person gets a Linux login (SSH keys pulled from github.com/<username>.keys) and,
# once the services are running, an Airflow, MLflow and database login sharing one
# password. The password is written to ~/ACCOUNTS.txt in their home folder on the server.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEAM_FILE="$REPO_DIR/server/team.txt"

if [[ $EUID -ne 0 ]]; then
	echo "Run this with sudo." >&2
	exit 1
fi
if [[ ! -f "$REPO_DIR/.env" ]]; then
	echo "No .env file in $REPO_DIR. Run server/setup.sh first." >&2
	exit 1
fi
set -a
# shellcheck source=/dev/null
. "$REPO_DIR/.env"
set +a

compose() { docker compose --project-directory "$REPO_DIR" "$@"; }

# GitHub usernames become Linux usernames, lower-cased.
linux_name() { tr '[:upper:]' '[:lower:]' <<<"$1"; }

new_password() { openssl rand -base64 18 | tr '+/' '-_'; }

services_running() {
	local service
	for service in postgres airflow-apiserver mlflow; do
		[[ "$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q "$service" 2>/dev/null)" 2>/dev/null)" == healthy ]] || return 1
	done
}

set_db_login() { # user password
	compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U postgres -d dhr \
		-v u="$1" -v p="$2" <<-'SQL'
		SELECT format('CREATE ROLE %I LOGIN IN ROLE team', :'u')
		WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'u') \gexec
		SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'u', :'p') \gexec
		SELECT format('ALTER ROLE %I SET role = %L', :'u', 'team') \gexec
		SQL
}

set_airflow_login() { # user password github-name is-admin
	local role=User
	[[ "$4" == yes ]] && role=Admin
	if compose exec -T airflow-apiserver airflow users list --output plain 2>/dev/null | awk '{print $2}' | grep -qx "$1"; then
		compose exec -T airflow-apiserver airflow users reset-password --username "$1" --password "$2" >/dev/null
	else
		compose exec -T airflow-apiserver airflow users create --username "$1" --password "$2" \
			--firstname "$3" --lastname "(GitHub)" --email "$3@users.noreply.github.com" --role "$role" >/dev/null
	fi
}

set_mlflow_login() { # user password is-admin
	compose exec -T -e MLFLOW_TRACKING_USERNAME=admin -e MLFLOW_TRACKING_PASSWORD="$MLFLOW_ADMIN_PASSWORD" \
		mlflow python - "$1" "$2" "$3" <<-'PY'
		import sys
		from mlflow.exceptions import MlflowException
		from mlflow.server.auth.client import AuthServiceClient
		user, password, is_admin = sys.argv[1], sys.argv[2], sys.argv[3] == "yes"
		client = AuthServiceClient("http://localhost:5000")
		try:
		    client.create_user(user, password)
		except MlflowException:
		    client.update_user_password(user, password)
		client.update_user_admin(user, is_admin)
		PY
}

create_app_logins() { # user github-name is-admin
	local user=$1 gh=$2 admin=$3 home="/home/$1" password
	password=$(new_password)
	set_db_login "$user" "$password"
	set_airflow_login "$user" "$password" "$gh" "$admin"
	set_mlflow_login "$user" "$password" "$admin"

	cat >"$home/ACCOUNTS.txt" <<-EOF
		Your logins for the DHR project server (one password for all three):

		  Username:  $user
		  Password:  $password

		  Airflow:   https://airflow.$DOMAIN
		  MLflow:    https://mlflow.$DOMAIN
		  Database:  database "dhr" on port 5432. On the server, just type: psql
		             From your laptop, use an SSH tunnel (see the README).
	EOF
	printf '127.0.0.1:5432:dhr:%s:%s\nlocalhost:5432:dhr:%s:%s\n' "$user" "$password" "$user" "$password" >"$home/.pgpass"
	if ! grep -q 'PGDATABASE=dhr' "$home/.bashrc" 2>/dev/null; then
		printf '\n# DHR project database\nexport PGHOST=127.0.0.1 PGDATABASE=dhr\n' >>"$home/.bashrc"
	fi
	chown "$user:$user" "$home/ACCOUNTS.txt" "$home/.pgpass" "$home/.bashrc"
	chmod 600 "$home/ACCOUNTS.txt" "$home/.pgpass"
	echo "  $user: Airflow, MLflow and database logins created (password in /home/$user/ACCOUNTS.txt)"
}

sync_team() {
	local gh role user keys admin app_logins=yes admins_with_keys=0
	if ! services_running; then
		app_logins=no
		echo "Services aren't running yet, so only SSH logins will be set up this time."
	fi

	# team.txt is read on file descriptor 3 so the docker commands below can't swallow it.
	while read -r gh role _ <&3; do
		[[ -z "${gh:-}" || "$gh" == \#* ]] && continue
		user=$(linux_name "$gh")
		admin=no
		[[ "${role:-}" == admin ]] && admin=yes

		if [[ ! "$user" =~ ^[a-z][a-z0-9-]{0,31}$ ]]; then
			echo "  Skipping $gh: can't be used as a Linux username." >&2
			continue
		fi
		if ! keys=$(curl -fsS --max-time 20 "https://github.com/$gh.keys"); then
			echo "  Skipping $gh: couldn't fetch their keys from GitHub (is the username right?)." >&2
			continue
		fi
		if [[ -z "$keys" ]]; then
			echo "  Skipping $gh: no SSH keys on their GitHub account. They can add one at https://github.com/settings/keys" >&2
			continue
		fi

		if ! id "$user" &>/dev/null; then
			useradd --create-home --shell /bin/bash --comment "GitHub $gh" "$user"
			echo "  $user: SSH login created"
		fi
		usermod --unlock --expiredate '' "$user" 2>/dev/null || true
		install -d -m 700 -o "$user" -g "$user" "/home/$user/.ssh"
		printf '%s\n' "$keys" >"/home/$user/.ssh/authorized_keys"
		chown "$user:$user" "/home/$user/.ssh/authorized_keys"
		chmod 600 "/home/$user/.ssh/authorized_keys"

		if [[ $admin == yes ]]; then
			usermod -aG sudo,docker "$user"
			echo "$user ALL=(ALL) NOPASSWD:ALL" >"/etc/sudoers.d/90-dhr-$user"
			chmod 440 "/etc/sudoers.d/90-dhr-$user"
			admins_with_keys=$((admins_with_keys + 1))
		else
			gpasswd -d "$user" docker &>/dev/null || true
			gpasswd -d "$user" sudo &>/dev/null || true
			rm -f "/etc/sudoers.d/90-dhr-$user"
		fi

		if [[ $app_logins == yes && ! -f "/home/$user/ACCOUNTS.txt" ]]; then
			create_app_logins "$user" "$gh" "$admin"
		fi
	done 3<"$TEAM_FILE"

	if [[ $admins_with_keys -eq 0 ]]; then
		echo "No admin in team.txt has SSH keys on GitHub. Add your key to GitHub before continuing." >&2
		return 1
	fi
}

remove_member() {
	local user
	user=$(linux_name "$1")
	if id "$user" &>/dev/null; then
		usermod --lock --expiredate 1 "$user"
		rm -f "/home/$user/.ssh/authorized_keys" "/etc/sudoers.d/90-dhr-$user"
		gpasswd -d "$user" docker &>/dev/null || true
		gpasswd -d "$user" sudo &>/dev/null || true
		pkill -KILL -u "$user" 2>/dev/null || true
	fi
	if services_running; then
		compose exec -T postgres psql -q -U postgres -d dhr -v u="$user" <<-'SQL' || true
			SELECT format('ALTER ROLE %I NOLOGIN', :'u')
			WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'u') \gexec
			SQL
		compose exec -T airflow-apiserver airflow users delete --username "$user" &>/dev/null || true
		compose exec -T -e MLFLOW_TRACKING_USERNAME=admin -e MLFLOW_TRACKING_PASSWORD="$MLFLOW_ADMIN_PASSWORD" mlflow \
			python -c "import sys; from mlflow.server.auth.client import AuthServiceClient as C; C('http://localhost:5000').delete_user(sys.argv[1])" "$user" &>/dev/null || true
	fi
	echo "$user has been locked out. Their home folder and anything they made is kept."
}

case "${1:-}" in
sync) sync_team ;;
remove)
	[[ -n "${2:-}" ]] || { echo "Usage: $0 remove <github-username>" >&2; exit 1; }
	remove_member "$2"
	;;
*)
	echo "Usage: $0 sync | remove <github-username>" >&2
	exit 1
	;;
esac
