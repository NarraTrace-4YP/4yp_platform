# DHR project: shared environment

This repo holds everything the CS407 DHR project runs on: the server setup, the shared
Python environment, and (soon) the pipeline code. If it isn't in this repo, it isn't on
the server.

Admins setting up the server for the first time: see [docs/server-setup.md](docs/server-setup.md).

## The tools

| Tool | What it's for | Where it runs |
|---|---|---|
| GitHub | Home for all code. Every change goes in through a pull request | GitHub |
| GitHub Actions | Runs the checks on every pull request | GitHub |
| Docker Compose | Runs every service on the server from one file (`compose.yaml`) | Server |
| Caddy | Serves Airflow and MLflow over HTTPS | Server |
| PostgreSQL + pgvector + pg_search | The project database, with meaning search and keyword search built in | Server |
| Apache Airflow | Runs the pipeline steps, reading them straight from GitHub | Server |
| MLflow | Records every experiment run and LLM call | Server |
| LangGraph | Python library for building the agents | In the pipeline code |
| vLLM on Blythe | Runs the open-weight LLMs on the university's GPUs | Blythe |
| uv | Installs the same Python packages for everyone | Everywhere |
| Obsidian | Reading the LLM wiki | Your laptop |

## Getting access

1. Add an SSH key to your GitHub account if you haven't already:
   https://github.com/settings/keys (GitHub's guide: "Generating a new SSH key").
2. Ask an admin to add your GitHub username to `server/team.txt`.
3. Once they've done it, log in to the server and read your passwords:

   ```
   ssh <your-username>@<server-address>
   cat ~/ACCOUNTS.txt
   ```

   Your username is your GitHub username in **lower case** (GitHub's `TroubleBubble1` is
   `troublebubble1` here). Capitals give a "Permission denied (publickey)" error. If you
   have more than one SSH key, pick the one on GitHub with `ssh -i ~/.ssh/<key-file> ...`.

   One password covers Airflow, MLflow and the database.
4. Open Airflow at `https://airflow.<domain>` and MLflow at `https://mlflow.<domain>`.

## Working on your laptop

1. Install uv: https://docs.astral.sh/uv/getting-started/installation/
2. Clone the repo and install the packages:

   ```
   git clone https://github.com/<org>/<repo>.git
   cd <repo>
   uv sync
   ```

3. Run the tests: `uv run pytest`

To add a package, run `uv add <package>` and commit both `pyproject.toml` and `uv.lock`.

### Using the database from your laptop

The database isn't open to the internet. Reach it through SSH instead. Leave this running in
one terminal:

```
ssh -N -L 5432:127.0.0.1:5432 <your-username>@<server-address>
```

Then connect any database tool (DBeaver, VS Code, `psql`) to `localhost`, port `5432`,
database `dhr`, with your username and password. On the server itself, just type `psql`.

The database has three areas (schemas):

- `corpus`: the reviews and everything built from them. The wiki agent can read this.
- `evaluation`: case metadata and answer keys. The wiki agent can never read this, so
  evaluation answers can't leak into the wiki.
- `scratch`: experiments and temporary tables.

### Logging to MLflow from your laptop

Set these before running code that logs to MLflow:

```
export MLFLOW_TRACKING_URI=https://mlflow.<domain>
export MLFLOW_TRACKING_USERNAME=<your-username>
export MLFLOW_TRACKING_PASSWORD=<your password>
```

## How work reaches the server

1. Make a branch and do your work.
2. Open a pull request. The checks run (tests, lint, and "do the Airflow pipelines load").
3. Someone else reviews it. Once it's approved and the checks pass, merge it.
4. Within a minute, Airflow picks up the new version from `main`. Every run records the
   exact commit it used, so any result can be traced back to the code that produced it.

If you changed the Python packages (`pyproject.toml` / `uv.lock`), ask an admin to run
`sudo /opt/dhr/server/update.sh` so the server installs them.

## Where things go

- `dhr/`: the pipeline code itself, as an ordinary Python package.
- `dags/`: Airflow pipelines. Keep these thin: they only say what runs, in what order.
  `dags/environment_check.py` shows the pattern: a task runs a module from `dhr/` in the
  project's Python environment.
- `tests/`: tests for `dhr/`.
- `server/`, `postgres/`, `mlflow/`, `airflow/`, `caddy/`: the server setup. Only admins
  should need to touch these.

Never commit data or passwords. DHRC-100 comes from Hugging Face, and the pipeline loads it
into the database.

## The data

- **Loading DHRC-100:** the `load_dhrc` pipeline downloads the dataset from Hugging Face,
  checks every file against the dataset's checksums, and stores each review untouched in
  `corpus.documents`, recording which version of the dataset it came from.
- **Reading it:** `corpus.current_documents` always shows the latest version loaded. The
  review's original JSON is in the `raw` column.
- **Case metadata:** the `collect_case_metadata` pipeline asks the Home Office's
  [DHR Library](https://homicide-review.homeoffice.gov.uk/) which reviews match each of its
  search filters (victim's age, method of killing, relationship, ...), since the library
  has no download. It stores the answers in the `evaluation` schema, where the wiki agent
  can't see them. `evaluation.case_metadata` has one row per review of ours, with every
  detail as JSON; `evaluation.case_attributes` has one row per detail. A missing detail
  means "not returned", which can be "no" or "not recorded". The library is
  [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
  data, checked by people, but it warns that it may contain mistakes.
- **Changing the database:** add a new numbered file to `sql/migrations/` (e.g.
  `003_blocks.sql`). Pipelines apply new files automatically the next time they run. Never
  edit a file that's already been merged; add a new one instead.

## The LLM wiki

The wiki lives in its own private GitHub repo as Markdown files. Open that folder as a
vault in Obsidian. The Obsidian Git community plugin keeps it up to date. The wiki agent
commits every change, so the full history of what it wrote is kept.

## Not set up yet

- **Blythe connection.** Airflow can send jobs to Blythe over SSH, but first we need to
  check with the university's HPC team (SCRTP) that automated logins from an outside
  server are allowed.
