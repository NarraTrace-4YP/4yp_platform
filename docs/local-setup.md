# Running the full environment locally

The same Compose stack runs on a development computer and on the server. Locally it uses
the special `.localhost` domain, which modern browsers resolve to the current machine
without a hosts-file entry:

- landing page: <http://localhost>
- Windmill: <http://windmill.localhost>
- MLflow: <http://mlflow.localhost>

The local environment uses HTTP and local-only credentials. The server continues to use
its own `.env`, real domain and automatic HTTPS certificates.

## Requirements

Install [Git](https://git-scm.com/) and
[Docker Desktop](https://docs.docker.com/desktop/) (or Docker Engine with the Compose
plugin on Linux). Give Docker at least 8 GB of memory if possible; the complete stack is
designed for an 8 GB server and can take several minutes to start the first time.

Port 80 must be available for the port-free `localhost` addresses above. The local env
template uses less common host ports for HTTPS (`8443`), PostgreSQL (`15432`) and
Windmill's direct loopback API (`18000`) to avoid collisions; internal container ports
and all server defaults are unchanged.

## Start it

Clone the repository, then create your ignored local environment file.

On Windows PowerShell:

```powershell
git clone https://github.com/NarraTrace-4YP/4yp_platform.git
Set-Location 4yp_platform
Copy-Item .env.local.example .env.local
docker compose --env-file .env.local up -d --build
```

On macOS or Linux:

```bash
git clone https://github.com/NarraTrace-4YP/4yp_platform.git
cd 4yp_platform
cp .env.local.example .env.local
docker compose --env-file .env.local up -d --build
```

Wait until the services are ready:

```console
docker compose --env-file .env.local ps
```

Then open <http://localhost>. On a new local Windmill instance, use Windmill's initial
login (`admin@windmill.dev` / `changeme`) and change it when prompted. MLflow's local
login is `admin` / `local-admin-password`; you can change the local-only values in
`.env.local` before the first start.

The local databases and uploaded artifacts live in Docker volumes, so they survive
container restarts. They are separate from the server and never receive server data or
credentials.

## GitHub isolation

GitHub deployment and Windmill-to-GitHub synchronization are intentionally disabled for
local instances:

- no synchronization process runs in the Compose stack;
- `.env.local` has no deploy token or GitHub repository setting;
- `.github/windmill_sync.py` refuses to run outside GitHub Actions;
- edits made in local Windmill stay local and cannot create or rewrite GitHub branches or
  pull requests.

Do not copy `WINDMILL_TOKEN`, `WINDMILL_DEPLOY_TOKEN` or the production `WINDMILL_URL`
into `.env.local`. The repository's scheduled GitHub workflow continues to talk only to
the server URL and token stored in GitHub's repository settings.

## Make and test changes

Files under `site/` are bind-mounted, so a browser refresh shows landing-page edits
without rebuilding. After changing the Caddyfile, validate and reload it with:

```console
docker compose --env-file .env.local exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose --env-file .env.local exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

After changing Compose, MLflow or another image, apply the change with:

```console
docker compose --env-file .env.local up -d --build
```

Useful commands:

```console
docker compose --env-file .env.local logs --tail 100 caddy
docker compose --env-file .env.local logs --tail 100 windmill-server
docker compose --env-file .env.local logs --tail 100 mlflow
docker compose --env-file .env.local config --quiet
```

To stop the environment while keeping its data:

```console
docker compose --env-file .env.local down
```

To remove the local data too, use `docker compose --env-file .env.local down --volumes`.
That last command permanently deletes the local databases and artifacts.

## Local versus server deployment

Do not copy `.env.local` to the server. The two environments are selected only by their
env files:

| Setting | Local `.env.local` | Server `.env` |
|---|---|---|
| `DOMAIN` | `localhost` | the real domain |
| `PUBLIC_SCHEME` | `http` | `https` (also the default when omitted) |
| auxiliary host ports | `8443`, `15432`, `18000` | `443`, `5432`, `8000` (defaults) |
| secrets | disposable local values | generated private values |

On the server, continue to deploy infrastructure and landing-page changes with
`sudo /opt/dhr/server/update.sh`. That script still reads `/opt/dhr/.env`; it does not
read `.env.local`, and an older server `.env` without `PUBLIC_SCHEME` still defaults to
HTTPS. GitHub Actions deployment of `f/dhr` is unchanged.
