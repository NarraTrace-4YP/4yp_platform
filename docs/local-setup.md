# Running the Full Environment Locally

This guide explains how to run the complete DHR project platform on your own computer. It is intended for developers who need to test pipelines, modify the web interfaces or work on the platform's infrastructure without affecting the shared server.

The local environment uses the same Docker Compose configuration as the server. It runs PostgreSQL, Windmill, MLflow, Caddy and the project landing page, providing a separate environment for development and testing.

**The local environment is independent of the shared server.** It has its own databases, credentials and Windmill workspace. Changes made locally are not deployed to the server or automatically sent to GitHub.

## 1. Requirements

Before starting, install:

- [Git](https://git-scm.com/) to clone the repository and manage code changes.
- [Docker Desktop](https://docs.docker.com/desktop/) on Windows or macOS, or Docker Engine with the Compose plugin on Linux.

Docker should have at least 8 GB of memory available where possible. The complete platform is designed to run on an 8 GB server, although the first startup can take several minutes while Docker downloads and builds the container images.

### Local web addresses

The platform uses the special `.localhost` domain for its web interfaces.

| Service | Local address | Purpose |
|---|---|---|
| Landing page | http://localhost | Entry point for the project tools and applications. |
| Windmill | http://windmill.localhost | Runs and manages the project's pipelines. |
| MLflow | http://mlflow.localhost | Tracks experiments, model settings and results. |

Modern browsers resolve `.localhost` addresses to the computer they are running on. You therefore do not need to modify your hosts file or configure DNS.

Caddy routes requests to the correct Docker service based on the address.

**Port 80 must be available** for these addresses to work without specifying a port number.

The local configuration also uses alternative ports for services that might conflict with software already running on your computer:

| Service | Local host port |
|---|---|
| HTTPS | `8443` |
| PostgreSQL | `15432` |
| Windmill direct API | `18000` |

These ports are defined in `.env.local`. They only change how services are accessed from your computer. The ports used between Docker containers and the server's default configuration remain unchanged.

The local environment uses HTTP and development credentials. The shared server continues to use its real domain, HTTPS certificates and separately managed credentials.

## 2. Start the environment

The setup process has three parts:

1. Clone the project repository and create a local configuration file.
2. Start the Docker services.
3. Initialise Windmill with the project's scripts, flows and applications.

### Clone the repository

Open a terminal and run the commands for your operating system.

**Windows PowerShell**

```powershell
git clone https://github.com/NarraTrace-4YP/4yp_platform.git
Set-Location 4yp_platform
Copy-Item .env.local.example .env.local
docker compose --env-file .env.local up -d --build
```

**macOS or Linux**

```bash
git clone https://github.com/NarraTrace-4YP/4yp_platform.git
cd 4yp_platform
cp .env.local.example .env.local
docker compose --env-file .env.local up -d --build
```

These commands download the repository, create `.env.local` from the supplied example and start the services defined in `compose.yaml`.

The `.env.local` file contains the settings and credentials used by your local installation. It is ignored by Git, so its contents are not committed to the repository.

The `--env-file .env.local` argument tells Docker Compose to use these local settings rather than the shared server's configuration.

The `-d` option starts the containers in the background, while `--build` builds any images that need to be created or updated.

### Initialise Windmill

Starting Docker creates the services, but Windmill still needs the project's workspace, scripts and configuration.

Once the containers are healthy, run:

```bash
docker compose --env-file .env.local --profile local-tools run --rm --build windmill-bootstrap
```

This runs a temporary bootstrap container that prepares the local Windmill installation.

It performs the following tasks:

1. Creates the `dhr` workspace and its `f/dhr` folder.
2. Configures the local PostgreSQL, MLflow and Hugging Face settings as Windmill variables and secrets.
3. Imports the project's Python scripts from `f/dhr`.
4. Imports the Objective 1 (O1) pipeline and application.
5. Configures the public O1 application embed used by the landing page.

The bootstrap uses the code in your current repository checkout, so the local Windmill installation matches the version you are developing.

It also discovers the public share URL assigned to the O1 application and stores it in:

`runtime/o1.json`

The landing page reads this file to embed the application. This is necessary because Windmill generates a different share URL for each installation.

**The bootstrap only changes your local Windmill environment.** It does not create GitHub deployment accounts or tokens, and it cannot push changes to GitHub.

The repository is mounted read-only inside the bootstrap container, apart from the ignored `runtime/` directory. Any Windmill metadata needed during deployment is generated in a temporary container copy.

### Check that the services are running

To inspect the containers:

```bash
docker compose --env-file .env.local ps
```

The output shows each service and its current state.

Wait until the required services are healthy before trying to use the platform.

Once they are ready, open:

http://localhost

The landing page should provide access to the local Windmill and MLflow interfaces.

### Local login credentials

The local bootstrap replaces Windmill's temporary built-in administrator account with a local account.

Use the following credentials:

| Service | Username | Password |
|---|---|---|
| Windmill | `admin@local.invalid` | `WINDMILL_ADMIN_PASSWORD` in `.env.local` |
| MLflow | `admin` | `MLFLOW_ADMIN_PASSWORD` in `.env.local` |

These passwords are defined in your local configuration file. You can change the example values before the first startup if you prefer.

They are separate from the credentials used by the shared server.

### Troubleshooting an older checkout

An older version of the repository contained a PostgreSQL initialisation script with Windows line endings. This prevented the script from running correctly inside the Linux container.

If an initial startup reports that `postgres` is unhealthy, check its logs:

```bash
docker compose --env-file .env.local logs --tail 100 postgres
```

The relevant error is:

```text
20-project.sh: cannot execute: required file not found
```

If this occurs during a new, disposable local installation, update the repository and start again.

For PowerShell:

```powershell
docker compose --env-file .env.local down --volumes
git pull
docker compose --env-file .env.local up -d --build
```

Then rerun the Windmill bootstrap command from above.

PostgreSQL initialisation only runs when its data volume is first created. Removing the incomplete volume allows the corrected script to run during the next startup.

**Warning:** `down --volumes` permanently deletes the databases and other data stored in this local Compose project's volumes. Only use it here when the initial installation has failed and there is no local data you need to keep.

## 3. Local accounts and database access

The local environment uses a simpler account setup than the shared Ubuntu server.

On the server, `server/team.sh` creates Linux users, configures their GitHub SSH keys and provisions their application accounts.

None of this is necessary for local development.

### No SSH setup is required

A local deployment does not create Linux user accounts or generate `ACCOUNTS.txt`.

It also does not run an SSH server. You interact with the services directly from the computer running Docker.

**Do not run `server/setup.sh` or `server/team.sh` on your development computer.** These scripts are intended for configuring and managing the shared Ubuntu server.

If your computer already runs an SSH server, such as Windows OpenSSH, that is independent of this project. Its accounts and credentials are managed by your operating system.

### Local PostgreSQL access

The local PostgreSQL database uses these credentials:

| Setting | Value |
|---|---|
| Host | `127.0.0.1` |
| Port | `15432` by default |
| Database | `dhr` |
| Username | `postgres` |
| Password | `POSTGRES_PASSWORD` in `.env.local` |

The actual host port is controlled by `POSTGRES_HOST_PORT` in `.env.local`.

You can connect using a database client such as DBeaver, VS Code or `psql`.

Alternatively, open PostgreSQL's command-line interface directly inside its Docker container:

```bash
docker compose --env-file .env.local exec postgres psql -U postgres -d dhr
```

This connects to the local `dhr` database without requiring a separate network connection.

PostgreSQL is bound to `127.0.0.1`, so it is not exposed to other computers on your network.

The same principle applies to the `.localhost` web addresses. They refer to the computer running the browser, not necessarily the computer running Docker.

For example, opening `windmill.localhost` on a different laptop would refer to that laptop, not your development computer.

### Where local data is stored

PostgreSQL databases and uploaded artifacts are stored in Docker volumes.

These volumes persist when containers are stopped or recreated, allowing you to continue working without importing your data again.

The local volumes are independent of the shared server. They do not automatically receive data, passwords or configuration from the server.

## 4. GitHub and server isolation

One of the main requirements of local development is that it must not interfere with the shared environment.

The local Compose setup therefore disables GitHub deployment and Windmill-to-GitHub synchronisation.

The following safeguards are in place:

- No GitHub synchronisation process runs in the local Docker stack.
- `.env.local` contains no GitHub deployment token or repository deployment configuration.
- The `.github/windmill_sync.py` script refuses to run outside GitHub Actions.
- Changes made in local Windmill remain local. They cannot automatically create GitHub branches, open pull requests or overwrite repository files.

This differs from the shared server, where edits made through Windmill's web editor are detected by a scheduled GitHub Actions workflow and returned to the repository as a pull request.

### Keep production credentials separate

Do not copy any of the following into `.env.local`:

- `WINDMILL_TOKEN`
- `WINDMILL_DEPLOY_TOKEN`
- The production `WINDMILL_URL`

These values are used by the shared server's deployment process and should not be available to local development containers.

The scheduled GitHub workflow continues to use the server URL and deployment token stored in the repository's GitHub Actions settings.

Your local environment does not affect that workflow.

**Local development is intentionally one-way:** you can import your repository code into local Windmill, but changes made inside that local Windmill instance are not automatically sent back to GitHub.

If you want to contribute a change, make it in your repository checkout and submit it through the normal GitHub pull request process.

## 5. Making and testing changes

Different parts of the platform are updated in different ways.

Some files are mounted directly into their containers, so changes take effect immediately. Others require a container rebuild or a new Windmill bootstrap.

### Changing the landing page

Files under `site/` are bind-mounted into the local environment.

This means the container reads the files directly from your repository checkout rather than using a separate copy.

When you edit the landing page's HTML or CSS, refresh your browser to see the changes.

You do not need to rebuild the containers.

### Changing Caddy configuration

Caddy manages the web addresses and forwards requests to the correct services.

After editing the Caddyfile, first validate the configuration:

```bash
docker compose --env-file .env.local exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

If validation succeeds, reload Caddy:

```bash
docker compose --env-file .env.local exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Validating before reloading helps catch configuration errors before applying them to the running service.

### Changing Docker or service configuration

If you modify `compose.yaml`, MLflow or another service's image or build configuration, recreate the affected services using:

```bash
docker compose --env-file .env.local up -d --build
```

Docker Compose applies the updated configuration while preserving the existing data volumes.

### Changing Windmill scripts or applications

If you change anything under `f/dhr`, run the local Windmill bootstrap again:

```bash
docker compose --env-file .env.local --profile local-tools run --rm --build windmill-bootstrap
```

This updates the local Windmill installation to match the repository checkout, including:

- Python scripts and shared code.
- Windmill flows.
- The O1 application.
- Local variables and secrets.
- The landing page's O1 embed URL.

Run the same command after changing `HF_TOKEN` in `.env.local`, since the bootstrap updates the Hugging Face secret stored in Windmill.

**Be aware that bootstrapping can overwrite edits made directly to the same shared items in local Windmill.**

The repository checkout is treated as the source of truth for everything covered by `wmill.yaml`.

Personal scripts in `u/<username>` folders and unrelated folders are not part of that deployment and are left unchanged.

For shared pipeline development, it is therefore better to keep changes in the repository rather than relying on edits made only through Windmill's web editor.

## 6. Monitoring and troubleshooting

Docker Compose provides commands for checking service status, reading logs and validating configuration.

These are useful when a service fails to start or a web interface is not responding.

### Check container status

```bash
docker compose --env-file .env.local ps
```

This shows whether each container is running and, where configured, whether it is healthy.

### Read service logs

To view the latest 100 log lines for a service:

```bash
docker compose --env-file .env.local logs --tail 100 <service>
```

For example, the following commands inspect Caddy, Windmill and MLflow:

```bash
docker compose --env-file .env.local logs --tail 100 caddy
docker compose --env-file .env.local logs --tail 100 windmill-server
docker compose --env-file .env.local logs --tail 100 mlflow
```

These logs help distinguish between common problems:

- **Caddy errors:** Problems serving the local web addresses or forwarding requests.
- **Windmill errors:** Problems starting the application, connecting to its database or serving its API.
- **MLflow errors:** Problems starting the tracking service or accessing its storage.

For pipeline execution failures, also inspect the logs of the relevant Windmill worker and the failed run inside Windmill.

### Validate the Compose configuration

To check that Docker Compose can read the configuration:

```bash
docker compose --env-file .env.local config --quiet
```

This checks the configuration without starting or changing the containers.

It is useful after editing `compose.yaml` or related environment settings.

## 7. Stopping or resetting the environment

Docker Compose distinguishes between stopping the services and deleting their persistent data.

### Stop the environment without losing data

Run:

```bash
docker compose --env-file .env.local down
```

This stops and removes the project's containers and network, but keeps its data volumes.

When you start the platform again, PostgreSQL and the other services retain their existing data.

This is the normal way to shut down the local environment.

### Delete the local environment's data

If you want to completely reset the local installation, run:

```bash
docker compose --env-file .env.local down --volumes
```

This also deletes the project's Docker volumes, including the local databases and stored artifacts.

**This operation is destructive and cannot be undone without a separate backup.**

Use it only when you intentionally want to discard the local environment's data, such as after a failed first installation or when rebuilding a disposable test environment.

It does not delete the repository checkout or affect the shared server.

## 8. Differences between local and server deployment

Both environments use the same `compose.yaml`. Their behaviour is controlled by different environment files.

| Setting | Local `.env.local` | Server `.env` |
|---|---|---|
| `DOMAIN` | `localhost` | Real project domain |
| `PUBLIC_SCHEME` | `http` | `https` |
| HTTPS host port | `8443` | `443` |
| PostgreSQL host port | `15432` | `5432` |
| Windmill direct API host port | `18000` | `8000` |
| Credentials | Local development values | Generated private credentials |
| GitHub deployment | Disabled | Enabled through GitHub Actions |
| HTTPS certificates | Not required for local HTTP | Managed automatically |

The main differences are the domain, protocol, exposed ports and credentials.

### Environment selection

Docker Compose selects the appropriate configuration through the `--env-file` argument.

For local development:

```bash
docker compose --env-file .env.local up -d --build
```

On the shared server, the administration scripts use:

`/opt/dhr/.env`

**Do not copy `.env.local` onto the shared server.** It contains development settings that are unsuitable for the public deployment.

### Server updates remain separate

Infrastructure and landing-page changes on the shared server are applied using:

```bash
sudo /opt/dhr/server/update.sh
```

This script reads `/opt/dhr/.env` and does not use `.env.local`.

The server also supports older environment files where `PUBLIC_SCHEME` is not explicitly set. In that case, it defaults to HTTPS, preserving the existing deployment behaviour.

Pipeline scripts in `f/dhr` continue to deploy through GitHub Actions when changes are merged into `main`.

Local development does not change either of these deployment processes.

---

## 9. Quick reference

The commands below cover the most common local development tasks.

| Task | Command |
|---|---|
| Start or update the services | `docker compose --env-file .env.local up -d --build` |
| Import project code into Windmill | `docker compose --env-file .env.local --profile local-tools run --rm --build windmill-bootstrap` |
| Check running services | `docker compose --env-file .env.local ps` |
| View recent logs | `docker compose --env-file .env.local logs --tail 100 <service>` |
| Open PostgreSQL | `docker compose --env-file .env.local exec postgres psql -U postgres -d dhr` |
| Validate Compose configuration | `docker compose --env-file .env.local config --quiet` |
| Stop services and keep data | `docker compose --env-file .env.local down` |
| Stop services and delete local data | `docker compose --env-file .env.local down --volumes` |

### Recommended development workflow

For most changes, the process is straightforward:

1. Start the local environment and confirm the services are healthy.
2. Make your changes in the repository checkout.
3. Run any relevant tests.
4. Rebuild the affected services or rerun the Windmill bootstrap as needed.
5. Test the changes through the local web interfaces.
6. Commit your changes and open a GitHub pull request.

This allows you to develop and test the complete platform locally while keeping the shared server unaffected until changes have been reviewed and merged.
