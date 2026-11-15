#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu 24.04 server. Run it from the repo, as root:
#
#   git clone https://github.com/<org>/<repo>.git /opt/dhr
#   cd /opt/dhr && bash server/setup.sh
#
# The first run creates .env and stops so you can fill in your domain and repo name.
# Run it again and it does the rest. It is safe to re-run.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

step() { printf '\n== %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Waits up to 10 minutes for every service with a health check to report healthy.
wait_until_healthy() {
	local waiting=""
	for _ in $(seq 1 60); do
		waiting=$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy" {print $1}')
		[[ -z "$waiting" ]] && return 0
		sleep 10
	done
	die "Still not healthy after 10 minutes: $waiting. See: docker compose logs <service>"
}

[[ $EUID -eq 0 ]] || die "Run this as root (or with sudo)."
grep -q 'ID=ubuntu' /etc/os-release || die "This script expects Ubuntu."

# ---------------------------------------------------------------- settings
if [[ ! -f .env ]]; then
	cp .env.example .env
	chmod 600 .env
	echo "Created $REPO_DIR/.env."
	echo "Edit DOMAIN, GITHUB_REPO and ACME_EMAIL in it, then run this script again."
	exit 0
fi

set -a
# shellcheck source=/dev/null
. ./.env
set +a
[[ "$DOMAIN" != example.com && -n "$DOMAIN" ]] || die "Set DOMAIN in .env first."
[[ "$GITHUB_REPO" != your-org/your-repo && "$GITHUB_REPO" == */* ]] || die "Set GITHUB_REPO in .env first."
[[ "$ACME_EMAIL" != you@example.com && -n "$ACME_EMAIL" ]] || die "Set ACME_EMAIL in .env first."

step "Generating any missing secrets in .env"
fill_secret() { # name generator
	local current
	current=$(grep -E "^$1=" .env | cut -d= -f2-)
	if [[ -z "$current" ]]; then
		sed -i "s|^$1=.*|$1=$($2)|" .env
		echo "  generated $1"
	fi
}
random_text() { openssl rand -hex 24; }
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }
for name in POSTGRES_PASSWORD AIRFLOW_DB_PASSWORD MLFLOW_DB_PASSWORD PIPELINE_DB_PASSWORD \
	WIKI_DB_PASSWORD AIRFLOW_ADMIN_PASSWORD MLFLOW_ADMIN_PASSWORD MLFLOW_SECRET_KEY \
	AIRFLOW_SECRET_KEY AIRFLOW_JWT_SECRET; do
	fill_secret "$name" random_text
done
fill_secret AIRFLOW_FERNET_KEY fernet_key

# Load .env again so the secrets just generated are the ones used below.
set -a
# shellcheck source=/dev/null
. ./.env
set +a

# ---------------------------------------------------------------- system packages
step "Updating the system and installing basics"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl git openssl ufw unattended-upgrades postgresql-client
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
	# shellcheck source=/dev/null
	codename=$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
	echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
		>/etc/apt/sources.list.d/docker.list
	apt-get update -q
	apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
echo "  $(docker --version)"

# ---------------------------------------------------------------- people
step "Creating SSH logins for everyone in server/team.txt"
bash server/team.sh sync || die "Fix the problem above, then run this script again."

step "Locking down SSH (keys only, no root login)"
cat >/etc/ssh/sshd_config.d/00-dhr.conf <<-'EOF'
	# Set by server/setup.sh. Log in with the SSH key on your GitHub account.
	PermitRootLogin no
	PasswordAuthentication no
	KbdInteractiveAuthentication no
	PubkeyAuthentication yes
EOF
sshd -t || { rm -f /etc/ssh/sshd_config.d/00-dhr.conf; die "The SSH settings didn't validate; nothing was changed."; }
systemctl reload ssh || systemctl restart ssh

step "Turning on the firewall (SSH and web only)"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443 >/dev/null
ufw --force enable >/dev/null
ufw status | sed 's/^/  /'

# ---------------------------------------------------------------- services
step "Starting the services (the first build takes a few minutes)"
chown root:docker .env
chmod 640 .env
docker compose up -d --build || die "Services didn't start. See: docker compose ps, and docker compose logs <service>"
wait_until_healthy
docker compose ps --format 'table {{.Service}}\t{{.Status}}' | sed 's/^/  /'

step "Creating Airflow, MLflow and database logins for the team"
bash server/team.sh sync

cat <<-EOF

	Done.

	  Airflow:  https://airflow.$DOMAIN   (admin login: admin / AIRFLOW_ADMIN_PASSWORD in .env)
	  MLflow:   https://mlflow.$DOMAIN    (admin login: admin / MLFLOW_ADMIN_PASSWORD in .env)

	Before closing this session, open a NEW terminal and check you can log in as yourself:
	  ssh <your-github-username>@<this-server>
	Root login and passwords are now switched off.

	If the web addresses don't load yet, check that airflow.$DOMAIN and mlflow.$DOMAIN
	both point at this server in your DNS. HTTPS starts working once they do.
EOF
