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

Once the services are healthy, provision local Windmill and import this checkout's
`f/dhr` scripts, O1 flow and O1 app:

```console
docker compose --env-file .env.local --profile local-tools run --rm --build windmill-bootstrap
```

This explicit local-only command creates the `dhr` workspace and folder, stores the local
database, MLflow and Hugging Face settings, and pushes the project code. It does not
create a GitHub deploy account or token and cannot push to GitHub. The checkout is mounted
read-only except for the ignored `runtime/` directory. Windmill metadata is generated in
a temporary container copy, and `runtime/o1.json` receives this local Windmill instance's
public O1 share URL so the landing-page embed works automatically.

Wait until the services are ready:

```console
docker compose --env-file .env.local ps
```

If a first start made with an older checkout reports that `postgres` is unhealthy and
its log says `20-project.sh: cannot execute: required file not found`, update the repo
before continuing. That checkout used Windows line endings in a Linux container. Because
PostgreSQL initialization is one-time, remove that incomplete first-start data and start
again:

```powershell
docker compose --env-file .env.local down --volumes
git pull
docker compose --env-file .env.local up -d --build
```

Only use `down --volumes` for a failed, disposable local initialization: it permanently
deletes all databases and artifacts belonging to this local Compose project.

Then open <http://localhost>. The local bootstrap command replaces Windmill's temporary
built-in login with `admin@local.invalid`; its password is `WINDMILL_ADMIN_PASSWORD` from
`.env.local`. MLflow's local login is `admin` with `MLFLOW_ADMIN_PASSWORD` from that same
file. Change the local-only values before the first start if desired.

The local databases and uploaded artifacts live in Docker volumes, so they survive
container restarts. They are separate from the server and never receive server data or
credentials.

## Local accounts and SSH

Local deployment does **not** generate `ACCOUNTS.txt`. That file is created only by
`server/team.sh` on the shared Ubuntu server, where the script also creates Linux users,
installs their GitHub SSH keys and provisions their application accounts. Do not run
`server/setup.sh` or `server/team.sh` on a development computer.

The local stack does not contain or expose an SSH server, so there is no project-provided
SSH username or password. Work with it directly on the computer running Docker. If that
computer independently has Windows OpenSSH or another SSH server enabled, its login is
your operating-system account and is outside this project.

Local service credentials are:

| Service | Local login |
|---|---|
| Windmill | `admin@local.invalid` / the `WINDMILL_ADMIN_PASSWORD` value in `.env.local` |
| MLflow | user `admin` / the `MLFLOW_ADMIN_PASSWORD` value in `.env.local` |
| PostgreSQL | user `postgres` / the `POSTGRES_PASSWORD` value in `.env.local` |

PostgreSQL is available only on the local computer at `127.0.0.1`, using the
`POSTGRES_HOST_PORT` in `.env.local` (`15432` in the example), with database `dhr`. You
can also open its command line without a network login:

```console
docker compose --env-file .env.local exec postgres psql -U postgres -d dhr
```

The `.localhost` web addresses are also intended for the same computer: on another
device, `windmill.localhost` would refer to that other device, not to this Docker host.

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

After changing anything under `f/dhr`, or after changing `HF_TOKEN` in `.env.local`,
refresh local Windmill with:

```console
docker compose --env-file .env.local --profile local-tools run --rm --build windmill-bootstrap
```

The command updates the local Windmill variables plus the `f/dhr` scripts, flow and app
to match the checkout, and refreshes the generated landing-page embed URL. It may
overwrite edits made directly to those same items in local
Windmill; personal `u/...` items and unrelated folders are outside `wmill.yaml` and are
not touched.

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
