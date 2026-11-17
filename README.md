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
  and every past run is kept under **Runs**. Everyone can run them, and edit them either
  on GitHub or in Windmill's web editor (see "How work reaches the server").
- **The O1 app** (`f/dhr/o1`, under **Apps**) is the place to start. It explains O1 and
  where it stands, shows the datasets and where their reviews came from, runs each
  pipeline (with its progress and log as it runs), and has the case viewer (every case
  detail the DHR Library holds for each review) and attribute counts (how each dataset
  compares with the whole library). It's a small React app in `f/dhr/o1.raw_app`,
  deployed like everything else; its backend is the `o1_*` and `case_viewer_*` scripts.
- **The O1 pipeline** (`f/dhr/o1_pipeline`, a Windmill flow) runs every built O1 step in
  order: load DHRC-100, load DHRC-RAW, collect the case metadata, group each dataset's
  blocks by section, then report where O1 stands. Each step can be switched off. New O1
  steps get added to it as they're built.
- **Your own space** is the folder `u/<your-username>`. Write and run anything there in
  the web editor (**+ Script**, choose Python). Your scripts can use the team's code:
  `from f.dhr.db import connect` gives you the project database.
- **Making something a team pipeline:** copy it into this repo as `f/dhr/<name>.py`, check
  it follows the rules below, and open a pull request. If it's an O1 step, also add it to
  `f/dhr/o1_pipeline.flow/flow.yaml` and explain it in the O1 app (`Overview.tsx` and
  `RunTab.tsx`); the tests check the flow and the app match the scripts.

## How work reaches the server

1. Make a branch and do your work.
2. Open a pull request. The checks run: tests and lint, "can Windmill read the scripts"
   (and the flows and apps), and "is the server config valid".
3. Someone else reviews it. Once it's approved and the checks pass, merge it.
4. Merging starts the **deploy** (GitHub **Actions** tab). Within a couple of minutes it
   pushes `f/dhr` to Windmill. Each script's history in Windmill names the commit it came
   from, and the pipelines record that commit next to everything they store, so any
   result can be traced back to the code that produced it.

**Editing in Windmill instead.** You can also change anything in `f/dhr` in Windmill's web
editor. It runs straight away. Within about 15 minutes a GitHub workflow (**windmill to
github**) notices and opens a pull request called **Changes made in Windmill**, with the
checks run on it; review and merge it like any other. (To send edits sooner: **Actions** >
**windmill to github** > **Run workflow**.) A few things to know:

- Until that pull request is merged, deploys stop rather than overwrite the edits, and say
  why. Merging it deploys everything on main, including whatever was waiting.
- Don't push to the `windmill-changes` branch: it's rebuilt from Windmill every 15 minutes.
  To change something in it, edit it in Windmill again.
- If the same file was also changed on GitHub since the last deploy, the pull request says
  so: check the GitHub change hasn't been undone.
- Leave the `windmill-snapshot` branch alone: it records what Windmill held after the last
  deploy, which is how edits are spotted (`.github/windmill_sync.py`).

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
  (e.g. `005_template_matches`). Pipelines apply new migrations the next time they run. Never edit
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

Never commit data or passwords. The datasets come from Hugging Face, and the pipeline loads
them into the database.

## The data

- **Two datasets, kept apart:** DHRC-100 is the 100 human-validated reviews. DHRC-RAW is
  all 702 reviews gathered (including raw versions of the same 100, under the same names),
  as automated output nobody has checked by hand. They're listed in `f/dhr/datasets.py`.
- **Loading one:** the `f/dhr/load_dhrc` script (choose the dataset in its form) downloads
  it from Hugging Face, checks every file against the dataset's checksums, and stores each
  review untouched in `corpus.documents`, labelled with its dataset and the version it
  came from. In DHRC-RAW a review that doesn't read as a document is skipped and listed
  in the log; in DHRC-100 it stops the load.
- **Reading it:** `corpus.current_documents` shows the latest version of each dataset,
  with a `dataset` column: add `WHERE dataset = 'DHRC-100'` (or `'DHRC-RAW'`) to pick one.
  The review's original JSON is in the `raw` column.
- **Sections (the hierarchical representation):** the `f/dhr/group_sections` script puts
  each review's blocks back in reading order under the headings they belong to, so a
  section that runs across a page break is one section. For DHRC-100 the structure comes
  from the headings the team checked by hand; for DHRC-RAW from Datalab's own, unchecked
  headings. Run it after loading a dataset (reloading removes the old grouping).
  `corpus.current_sections` has one row per section (heading, level, `parent_id`, pages)
  and `corpus.current_blocks` one row per block in reading order (section, page, HTML and
  plain `text`). Page headers and footers are left out. To get a section with everything
  nested inside it:

  ```sql
  SELECT * FROM corpus.section_blocks('DHRC-100', '<doc_id>', '<section_id>');  -- block by block
  SELECT corpus.section_text('DHRC-100', '<doc_id>', '<section_id>');           -- as one text
  ```

  On a laptop, `uv run python -m f.dhr.group_sections --file <review.json>` prints one
  review file's outline without touching the database.
- **Template candidates (towards the standardised representation):** the
  `f/dhr/find_template_candidates` script gives every section a short list of the report
  template sections it might be. The templates are downloaded from GOV.UK and read each
  run (`f/dhr/templates.py`), with each file's sha256 stored: the 2016 DHR guidance PDF
  (overview report, executive summary, IMRs, action plan; the default, as in the
  specification) or the September 2026 DARDR guidance (the DARDR template from its own
  Word file, and the action plan, scoping review and IMR from the guidance PDF). A section's
  candidates are the template sections whose headings are most alike its own (fuzzy word
  match, after dropping numbering) and those whose guidance text is most alike its text
  (embeddings from a small open model, `BAAI/bge-small-en-v1.5`, run on the server's CPU).
  Run it after grouping a dataset (regrouping removes the old candidates): about 5 minutes
  for DHRC-100, 40 for DHRC-RAW. A dataset keeps its candidates against each guidance.

  ```sql
  SELECT section_heading, template_title, template_heading, heading_score, text_score
  FROM corpus.current_template_candidates
  WHERE dataset = 'DHRC-100' AND guidance = 'dhr-2016' AND doc_id = '<doc_id>'
  ORDER BY section_position, text_rank;
  ```

  The next O1 step has an LLM choose among each section's candidates, or flag it as
  matching none.
- **Case metadata:** the `f/dhr/collect_case_metadata` script asks the Home Office's
  [DHR Library](https://homicide-review.homeoffice.gov.uk/) which reviews match each of its
  search filters (victim's age, method of killing, relationship, ...), since the library
  has no download. It stores the answers in the `evaluation` schema, where the wiki agent
  can't see them. It collects the whole library, so one run covers both datasets.
  `evaluation.case_metadata` has one row per review per dataset, with every detail as
  JSON; `evaluation.case_attributes` has one row per detail. Both have a `dataset` column.
  A missing detail means "not returned", which can be "no" or "not recorded". Reviews
  published elsewhere have no details: the `source` column says where each review came
  from (`Home Office website`, `HALT` or `Elsewhere`). The library is
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
