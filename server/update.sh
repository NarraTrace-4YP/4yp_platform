#!/usr/bin/env bash
# Brings the server up to date with the main branch. Only needed when the server setup
# or the Python packages change (pipeline code in dags/ and dhr/ is picked up by Airflow
# automatically). Run as an admin:  sudo /opt/dhr/server/update.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

git pull --ff-only
docker compose up -d --build

echo "Waiting for services to report healthy..."
for _ in $(seq 1 60); do
	waiting=$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$2 != "" && $2 != "healthy" {print $1}')
	[[ -z "$waiting" ]] && break
	sleep 10
done
if [[ -n "$waiting" ]]; then
	echo "Still not healthy after 10 minutes: $waiting. See: docker compose logs <service>" >&2
	exit 1
fi
docker compose ps --format 'table {{.Service}}\t{{.Status}}'
bash server/team.sh sync
