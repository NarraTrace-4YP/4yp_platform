#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu server (24.04 or newer). Run it from the repo:
#
#   sudo git clone https://github.com/<org>/<repo>.git /opt/dhr
#   cd /opt/dhr && sudo bash server/setup.sh
#
# The first time, it asks for your domain and an email address, then does everything
# else in one go. It is safe to run again: anything already done is left alone.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"

step() { printf '\n== %s\n' "$*"; }
die() {
	printf '\nERROR: %s\n' "$*" >&2
	exit 1
}

# ---------------------------------------------------------------- settings (.env)

env_value() { # name -> its value in .env (empty if missing)
	grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true
}

set_env_value() { # name value
	local value=${2//\\/\\\\}
	value=${value//&/\\&}
	value=${value//|/\\|}
	if grep -qE "^$1=" "$ENV_FILE"; then
		sed -i "s|^$1=.*|$1=$value|" "$ENV_FILE"
	else
		printf '%s=%s\n' "$1" "$2" >>"$ENV_FILE"
	fi
}

repo_from_git() { # "org/repo" from the clone's GitHub address, if there is one
	local url
	url=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)
	if [[ $url =~ github\.com[:/]([^/]+/[^/]+)$ ]]; then
		echo "${BASH_REMATCH[1]%.git}"
	fi
}

ask_setting() { # name question default placeholder
	local current answer
	current=$(env_value "$1")
	[[ -n "$current" && "$current" != "$4" ]] && return 0
	[[ -t 0 ]] || die "Set $1 in $ENV_FILE, then run this again."
	while true; do
		if [[ -n "$3" ]]; then
			read -rp "  $2 [$3]: " answer
			answer=${answer:-$3}
		else
			read -rp "  $2: " answer
		fi
		answer=${answer//[[:space:]]/}
		answer=${answer#http://}
		answer=${answer#https://}
		answer=${answer%/}
		[[ -n "$answer" && "$answer" != "$4" ]] && break
	done
	set_env_value "$1" "$answer"
}

prepare_settings() {
	step "Settings"
	if [[ ! -f "$ENV_FILE" ]]; then
		cp "$REPO_DIR/.env.example" "$ENV_FILE"
	fi
	chmod 600 "$ENV_FILE"
	ask_setting DOMAIN "Your domain (Windmill will be at windmill.<domain>)" "" example.com
	ask_setting GITHUB_REPO "GitHub repo, as organisation/repo" "$(repo_from_git)" your-org/your-repo
	ask_setting ACME_EMAIL "Email address for the HTTPS certificates" "" you@example.com
	[[ "$(env_value ACME_EMAIL)" == *@* ]] || die "ACME_EMAIL in $ENV_FILE doesn't look like an email address."
	echo "  Using domain $(env_value DOMAIN) and repo $(env_value GITHUB_REPO)"
	if [[ -z "$(env_value HF_TOKEN)" && -t 0 ]]; then
		local token
		read -rp "  Hugging Face token that can read DHRC-100 (press Enter to add it later): " token
		token=${token//[[:space:]]/}
		[[ -n "$token" ]] && set_env_value HF_TOKEN "$token"
	fi

	local name
	for name in POSTGRES_PASSWORD WINDMILL_DB_PASSWORD MLFLOW_DB_PASSWORD PIPELINE_DB_PASSWORD \
		WIKI_DB_PASSWORD MLFLOW_ADMIN_PASSWORD MLFLOW_SECRET_KEY WINDMILL_ADMIN_PASSWORD; do
		if [[ -z "$(env_value "$name")" ]]; then
			set_env_value "$name" "$(openssl rand -hex 24)"
			echo "  generated $name"
		fi
	done

	# Load the settings only now, once every value is final, so Docker gets the real ones.
	set -a
	# shellcheck source=/dev/null
	. "$ENV_FILE"
	set +a
}

# ---------------------------------------------------------------- steps

fix_file_modes() {
	# Uploading files through the GitHub website drops the "executable" mark; put it back.
	chmod +x "$REPO_DIR"/server/*.sh "$REPO_DIR/postgres/20-project.sh" "$REPO_DIR/mlflow/start.sh"
	if [[ -d "$REPO_DIR/.git" ]]; then
		git -C "$REPO_DIR" config core.fileMode false
	fi
}

install_system() {
	step "Updating the system and installing basics"
	export DEBIAN_FRONTEND=noninteractive
	apt-get update -q
	apt-get upgrade -yq
	apt-get install -yq ca-certificates curl git jq openssl ufw unattended-upgrades postgresql-client
	dpkg-reconfigure -f noninteractive unattended-upgrades

	step "Adding 4 GB of swap (a safety net for the 8 GB of memory)"
	if ! swapon --show | grep -q .; then
		fallocate -l 4G /swapfile
		chmod 600 /swapfile
		mkswap /swapfile >/dev/null
		swapon /swapfile
		grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
		echo "  swap added"
	else
		echo "  swap already present"
	fi

	step "Installing Docker"
	if ! command -v docker >/dev/null; then
		install -m 0755 -d /etc/apt/keyrings
		curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
		chmod a+r /etc/apt/keyrings/docker.asc
		local codename
		# shellcheck source=/dev/null
		codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
		echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
			>/etc/apt/sources.list.d/docker.list
		apt-get update -q
		apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
	fi
	systemctl enable --now docker
	echo "  $(docker --version)"
}

secure_logins() {
	step "Creating SSH logins for everyone in server/team.txt"
	bash "$REPO_DIR/server/team.sh" sync || die "Fix the problem above, then run this script again."

	step "Locking down SSH (keys only, no root login)"
	cat >/etc/ssh/sshd_config.d/00-dhr.conf <<-'EOF'
		# Set by server/setup.sh. Log in with the SSH key on your GitHub account.
		PermitRootLogin no
		PasswordAuthentication no
		KbdInteractiveAuthentication no
		PubkeyAuthentication yes
	EOF
	if ! sshd -t; then
		rm -f /etc/ssh/sshd_config.d/00-dhr.conf
		die "The SSH settings didn't validate; nothing was changed."
	fi
	systemctl reload ssh || systemctl restart ssh
	cat <<-'EOF'

		  Password logins are now switched off. Keep this window open, and in a NEW
		  terminal check you can still get in:  ssh <your-username>@<this-server>
		  (your username is your GitHub username in lower case)
	EOF

	step "Turning on the firewall (SSH and web only)"
	ufw default deny incoming >/dev/null
	ufw default allow outgoing >/dev/null
	ufw allow OpenSSH >/dev/null
	ufw allow 80/tcp >/dev/null
	ufw allow 443 >/dev/null
	ufw --force enable >/dev/null
	ufw status | sed 's/^/  /'
}

wait_until_healthy() { # up to 10 minutes
	local waiting=""
	for _ in $(seq 1 60); do
		waiting=$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy" {print $1}')
		[[ -z "$waiting" ]] && return 0
		sleep 10
	done
	die "Still not healthy after 10 minutes: $waiting. See: docker compose logs <service>"
}

start_services() {
	step "Starting the services (the first start downloads about 3 GB and takes 5 to 10 minutes)"
	chown root:docker "$ENV_FILE"
	chmod 640 "$ENV_FILE"
	docker compose up -d --build || die "Services didn't start. See: docker compose ps, and docker compose logs <service>"
	echo "  waiting for every service to report healthy..."
	wait_until_healthy
	docker compose ps --format 'table {{.Service}}\t{{.Status}}' | sed 's/^/  /'

	step "Setting up Windmill (admin account, workspace, pipeline settings, deploy token)"
	bash "$REPO_DIR/server/windmill.sh" configure

	step "Creating Windmill, MLflow and database logins for the team"
	bash "$REPO_DIR/server/team.sh" sync
}

main() {
	[[ $EUID -eq 0 ]] || die "Run this with sudo."
	grep -q '^ID=ubuntu' /etc/os-release || die "This script expects Ubuntu."
	cd "$REPO_DIR"

	fix_file_modes
	prepare_settings
	install_system
	secure_logins
	start_services

	# shellcheck source=/dev/null
	. "$ENV_FILE"
	cat <<-EOF

		Done.

		  Landing page: https://$DOMAIN
		  Windmill:     https://windmill.$DOMAIN
		  MLflow:       https://mlflow.$DOMAIN
		  Your own logins are in ~/ACCOUNTS.txt when you SSH in as yourself.
		  Server admin logins (Windmill: admin@$DOMAIN, MLflow: admin) are in $ENV_FILE.

		One last step, so merging to main deploys the pipelines to Windmill. In GitHub, open
		https://github.com/$GITHUB_REPO/settings/secrets/actions and add:
		  - on the "Secrets" tab, a secret named WINDMILL_TOKEN with this value:
		      $WINDMILL_DEPLOY_TOKEN
		  - on the "Variables" tab, a variable named WINDMILL_URL with this value:
		      https://windmill.$DOMAIN/
		(The token is also kept in $ENV_FILE as WINDMILL_DEPLOY_TOKEN.)

		If the web addresses don't load yet, check that $DOMAIN, windmill.$DOMAIN and
		mlflow.$DOMAIN all point at this server in your DNS. HTTPS starts working once they do.
	EOF
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
	main "$@"
fi