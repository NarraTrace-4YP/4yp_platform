# DHR project: shared environment

This repo holds everything the CS407 DHR project runs on: the server setup, the shared
Python environment, and the pipeline code. If it isn't in this repo, it isn't on the
server.

Admins setting up the server for the first time: see [docs/server-setup.md](docs/server-setup.md).

## The tools

| Tool | What it's for | Where it runs |
|---|---|---|
| GitHub | Home for all code. Every change goes in through a pull request | GitHub |
| GitHub Actions | Runs the checks on every pull request, and deploys `main` to Windmill | GitHub |
| Docker Compose | Runs every service on the server from one file (`compose.yaml`) | Server |
| Caddy | Serves Windmill and MLflow over HTTPS | Server |
| PostgreSQL + pgvector + pg_search | The project database, with meaning search and keyword search built in | Server |
| Windmill | Runs the pipeline scripts, and has a web editor for trying things out | Server |
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

   One password covers Windmill, MLflow and the database.
4. Open Windmill at `https://windmill.<domain>` (log in with the email address in
   `ACCOUNTS.txt`) and MLflow at `https://mlflow.<domain>`.

## Using Windmill

- **The team's pipelines** are in the folder `f/dhr`. Open one (for example
  `f/dhr/load_dhrc`), fill in the form, and click **Run**. The log appears as it runs,
  and every past run is kept under **Runs**. Everyone can run these, but no one can edit
  them in Windmill: they come from GitHub (see below), so GitHub always has the real
  version.
- **The case viewer** (`f/dhr/case_viewer`, under **Apps**) shows every case detail the
  DHR Library holds for each of our reviews, with a search for reviews and for details.
  It's a small React app in `f/dhr/case_viewer.raw_app`, deployed like everything else.
- **Your own space** is the folder `u/<your-username>`. Write and run anything there in
  the web editor (**+ Script**, choose Python). Your scripts can use the team's code:
  `from f.dhr.db import connect` gives you the project database.
- **Making something a team pipeline:** copy it into this repo as `f/dhr/<name>.py`, check
  it follows the rules below, and open a pull request.

## How work reaches the server

1. Make a branch and do your work.
2. Open a pull request. The checks run: tests and lint, "can Windmill read the scripts",
   and "is the server config valid".
3. Someone else reviews it. Once it's approved and the checks pass, merge it.
4. Merging starts the **deploy** (GitHub **Actions** tab). Within a couple of minutes it
   pushes `f/dhr` to Windmill. Each script's history in Windmill names the commit it came
   from, and the pipelines record that commit next to everything they store, so any
   result can be traced back to the code that produced it.

## Writing a pipeline script

A script in `f/dhr` is an ordinary Python file that Windmill can run. The tests check
these rules, so a pull request that breaks one fails its checks:

- **`main` is what Windmill runs.** Its arguments become the form you fill in, so give
  each one a type and a plain default (`revision: str = "main"`, not a constant's name).
  Whatever `main` returns is shown as the result. To fail a run, raise an exception with
  a message saying what went wrong.
- **Packages go in the block at the top** (`# /// script ... # ///`), pinned to the exact
  version in `uv.lock`, so Windmill uses the same packages as your laptop. To add one,
  run `uv add <package>`, then copy its version from `uv.lock` into the block.
- **Shared code** (a file with no `main`, like `db.py`) is imported as
  `from .db import connect`. Windmill can't follow `from . import db`.
- **Passwords never go in code.** `settings.py` reads them from Windmill's secret
  variables, or from environment variables on your laptop.
- **Changing the database:** add a new entry at the end of `f/dhr/migrations.py`
  (e.g. `003_blocks`). Pipelines apply new migrations the next time they run. Never edit
  one that's already been merged; add a new one instead.

## Working on your laptop

1. Install uv: https://docs.astral.sh/uv/getting-started/installation/
2. Clone the repo and install the packages:

   ```
   git clone https://github.com/<org>/<repo>.git
   cd <repo>
   uv sync
   ```

3. Run the tests: `uv run pytest`

You can run a pipeline on your laptop too, against the real database through the SSH
tunnel below: set `DHR_DATABASE_URL=postgresql://<your-username>:<password>@localhost:5432/dhr`
and run, for example, `uv run python -m f.dhr.collect_case_metadata --max-filters 10 --dry-run`.

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

## Where things go

- `f/dhr/`: the team's pipelines, as Windmill scripts, plus the code they share.
- `tests/`: tests for `f/dhr/`.
- `wmill.yaml`, `.github/workflows/deploy.yml`: how `main` is deployed to Windmill.
- `server/`, `postgres/`, `mlflow/`, `caddy/`, `compose.yaml`: the server setup. Only
  admins should need to touch these.

Never commit data or passwords. DHRC-100 comes from Hugging Face, and the pipeline loads it
into the database.

## The data

- **Loading DHRC-100:** the `f/dhr/load_dhrc` script downloads the dataset from Hugging
  Face, checks every file against the dataset's checksums, and stores each review
  untouched in `corpus.documents`, recording which version of the dataset it came from.
- **Reading it:** `corpus.current_documents` always shows the latest version loaded. The
  review's original JSON is in the `raw` column.
- **Case metadata:** the `f/dhr/collect_case_metadata` script asks the Home Office's
  [DHR Library](https://homicide-review.homeoffice.gov.uk/) which reviews match each of its
  search filters (victim's age, method of killing, relationship, ...), since the library
  has no download. It stores the answers in the `evaluation` schema, where the wiki agent
  can't see them. `evaluation.case_metadata` has one row per review of ours, with every
  detail as JSON; `evaluation.case_attributes` has one row per detail. A missing detail
  means "not returned", which can be "no" or "not recorded". The library is
  [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/)
  data, checked by people, but it warns that it may contain mistakes.

## The LLM wiki

The wiki lives in its own private GitHub repo as Markdown files. Open that folder as a
vault in Obsidian. The Obsidian Git community plugin keeps it up to date. The wiki agent
commits every change, so the full history of what it wrote is kept.

## Not set up yet

- **Blythe connection.** A Windmill script can submit jobs to Blythe over SSH, but first
  we need to check with the university's HPC team (SCRTP) that automated logins from an
  outside server are allowed.
