# DHR Project: Shared Development Environment

This repository contains the code and infrastructure for our CS407 Domestic Homicide Review (DHR) project. It includes the data processing pipelines, database, experiment tracking, development tools and server configuration.

The project aims to use large language models (LLMs) to identify patterns across DHRs. These reviews describe individual domestic homicides and the lessons identified by the agencies involved. Our goal is to organise their contents into a consistent structure, build a linked knowledge base from the evidence, and use it to analyse patterns across reviews.

The work follows four main stages:

1. **Prepare the data.** Load the reviews, reconstruct their section structure and map sections to a common report template.
2. **Build an LLM wiki.** Extract information about people, agencies, events and recommendations into linked pages, supported by citations to the original reviews.
3. **Analyse the reviews.** Use the wiki to investigate patterns across cases and compare the results with a conventional retrieval-augmented generation (RAG) approach.
4. **Evaluate the approach.** Test how well the pipeline works, how accurately the wiki represents its sources and whether the wiki improves analysis.

The platform provides a shared environment for developing and running these stages. Team members can work locally or use the shared server, while GitHub keeps the code and configuration consistent.

**GitHub is the source of truth for the project's code and configuration.** Datasets, passwords and other secrets are stored separately and must never be committed to the repository.

For detailed setup instructions, see:

- [Local setup](docs/local-setup.md) - Run the complete platform on your computer.
- [Server setup](docs/server-setup.md) - Deploy and maintain the shared server (admins only).

## 1. How the platform works

Most of the project runs on a small shared server. This hosts the database, pipeline orchestration, experiment tracking and web interfaces.

LLM inference requires more computing power, so GPU-intensive work will run separately on Blythe, the university's HPC system.

### The tools

| Tool | Purpose | Where it runs |
|---|---|---|
| **GitHub** | Stores the project's code and configuration. Changes are reviewed through pull requests. | GitHub |
| **GitHub Actions** | Tests changes, deploys approved code and synchronises edits made in Windmill. | GitHub |
| **Docker Compose** | Starts and manages the platform's services from `compose.yaml`. | Local / Server |
| **Caddy** | Provides access to Windmill, MLflow and the landing page, using HTTP locally and HTTPS on the server. | Local / Server |
| **PostgreSQL** | Stores the reviews, processed data and results. `pgvector` supports vector similarity search and `pg_search` supports keyword search. | Local / Server |
| **Windmill** | Runs, schedules and monitors Python pipelines. Also provides a web editor and project applications. | Local / Server |
| **MLflow** | Tracks experiments, model settings, LLM calls and their results. | Local / Server |
| **LangGraph** | Python library used to build agents that make multiple related LLM calls. | Pipeline code |
| **vLLM** | Serves open-weight LLMs on GPUs for inference. Integration with Blythe is planned. | Blythe |
| **uv** | Manages Python dependencies so everyone uses the same package versions. | Everywhere |
| **Obsidian** | Provides a convenient interface for reading and navigating the generated wiki. | Your laptop |

### How the services fit together

A typical pipeline starts in Windmill. It reads reviews or previously processed results from PostgreSQL, performs the required processing and writes the results back to the database.

For example, the first stage loads the source reviews, groups their text into sections and identifies possible matches to the standard report template.

When a task requires an LLM, the intention is for Windmill to coordinate the work while Blythe handles inference. This keeps GPU-intensive processing separate from the main server.

MLflow records how experiments were run, including the settings and code versions used. This allows results to be reproduced and compared.

The platform follows two important principles:

- **Reproducibility:** Results should be traceable to the code, data and settings that produced them.
- **Separation of concerns:** The main server manages data and pipelines, while Blythe handles GPU-intensive computation.

## 2. Running the platform locally

The complete platform can run on your computer through Docker Desktop. This provides a local version of Windmill, PostgreSQL, MLflow and the landing page, allowing you to develop and test without changing the shared server.

With Docker Desktop running, execute the following commands from the repository root in PowerShell:

```powershell
Copy-Item .env.local.example .env.local
docker compose --env-file .env.local up -d --build
docker compose --env-file .env.local --profile local-tools run --rm --build windmill-bootstrap
```

These commands:

1. Create your local configuration file.
2. Build and start the platform's containers.
3. Populate Windmill with the team's scripts, applications and configuration from `f/dhr`.

Once the services are running, open:

| Service | Local address |
|---|---|
| Landing page | http://localhost |
| Windmill | http://windmill.localhost |
| MLflow | http://mlflow.localhost |

For Windmill, sign in using `admin@local.invalid` and the `WINDMILL_ADMIN_PASSWORD` defined in `.env.local`.

For MLflow, use `admin` and the `MLFLOW_ADMIN_PASSWORD`.

### Updating your local environment

When you change scripts or applications under `f/dhr`, run the bootstrap again to update your local Windmill workspace:

```powershell
docker compose --env-file .env.local --profile local-tools run --rm --build windmill-bootstrap
```

Run this command after changing `HF_TOKEN` in `.env.local` as well, so the updated secret is available in Windmill.

The local bootstrap does not enable GitHub synchronisation. Changes made in your local Windmill instance therefore remain local.

Some environment settings are read directly when a container starts. If you change one of these, recreate the containers:

```powershell
docker compose --env-file .env.local up -d --force-recreate
```

**Changing a password in `.env.local` does not automatically change a password already stored by a service.** For example, to change the Windmill admin password, update it inside Windmill first, then update `WINDMILL_ADMIN_PASSWORD` to match.

The [full local setup guide](docs/local-setup.md) explains Linux and macOS commands, database access, stopping the services, troubleshooting and how local deployments remain isolated from the shared server.

## 3. Accessing the shared server

The shared server hosts the team's deployed environment. Each member receives an SSH account and credentials for Windmill, MLflow and PostgreSQL.

These steps apply to the shared server, not your local deployment.

### Getting an account

1. Add an SSH public key to your [GitHub account](https://github.com/settings/keys) if you haven't already.
2. Ask an admin to add your GitHub username to `server/team.txt`.
3. Once your account has been provisioned, connect to the server:

   ```bash
   ssh <your-username>@<server-address>
   ```

4. Read your account details:

   ```bash
   cat ~/ACCOUNTS.txt
   ```

Your server username is your GitHub username in **lowercase**. For example, `TroubleBubble1` becomes `troublebubble1`. Using capitals will result in a `Permission denied (publickey)` error.

If you have multiple SSH keys, specify the one registered with GitHub:

```bash
ssh -i ~/.ssh/<key-file> <your-username>@<server-address>
```

The account file contains the login details for the project services. One password is used for Windmill, MLflow and PostgreSQL.

### Accessing the web interfaces

Once your account is ready, open:

- **Windmill:** `https://windmill.<domain>`
- **MLflow:** `https://mlflow.<domain>`

For Windmill, sign in using the email address listed in `ACCOUNTS.txt`.

The [local setup guide](docs/local-setup.md#local-accounts-and-ssh) explains the separate credentials used by a local deployment.

## 4. Using Windmill

Windmill is where most project work is run. It provides a web interface for executing Python scripts, passing arguments, viewing progress and inspecting results.

It also keeps a history of previous runs, making it easier to investigate failures and reproduce experiments.

### Running a team pipeline

The team's scripts are stored in the `f/dhr` folder.

To run one:

1. Open Windmill and navigate to `f/dhr`.
2. Select a script, such as `f/dhr/load_dhrc`.
3. Fill in the input form.
4. Click **Run**.

Windmill displays the logs while the script runs. Once it finishes, its return value appears as the result. Previous executions remain accessible under **Runs**.

Scripts can be edited through GitHub or Windmill's web editor. The process for keeping these changes synchronised is explained in Section 5.

### The Objective 1 application

The main starting point for Objective 1 (O1) is the application at `f/dhr/o1`, under **Apps**.

O1 covers the preparation and standardisation of the source reviews. The application provides a single place to understand the work, inspect its data and run the relevant pipelines.

It includes:

- **Overview:** Explains O1, its planned stages and current progress.
- **Datasets:** Shows the available datasets and where their reviews came from.
- **Pipelines:** Runs each O1 processing step and displays its progress and logs.
- **Case viewer:** Displays the case details available from the Home Office DHR Library.
- **Dataset comparisons:** Shows how the attributes of each dataset compare with the wider Home Office library.

The application is a small React application defined in `f/dhr/o1.raw_app`. Its backend uses the `o1_*` and `case_viewer_*` scripts.

Like the Python pipelines, the application is maintained through GitHub and deployed automatically.

### Running the complete O1 pipeline

The `f/dhr/o1_pipeline` Windmill flow runs the existing O1 processing steps in order:

1. Load DHRC-100.
2. Load DHRC-RAW.
3. Collect the case metadata from the Home Office library.
4. Group each dataset's text blocks into sections.
5. Report the current status of O1.

Each step can be enabled or disabled, so you do not need to repeat earlier processing unnecessarily.

As new O1 steps are developed, they are added to the same flow. This provides a single entry point for running the complete pipeline.

### Working in your own space

Every team member has a personal Windmill folder:

`u/<your-username>`

Use this to create and test scripts before adding them to the shared pipelines.

To create a script, select **+ Script** and choose Python.

Your scripts can import the team's shared code. For example:

```python
from f.dhr.db import connect
```

This gives your script access to the project's database connection helper.

### Adding a script to the shared pipeline

Once a script is ready for the team to use:

1. Add it to the repository as `f/dhr/<name>.py`.
2. Follow the script conventions in Section 6.
3. Add tests where appropriate.
4. Open a pull request for review.

If it introduces a new O1 step, also update:

- `f/dhr/o1_pipeline.flow/flow.yaml` to include the step in the complete pipeline.
- `Overview.tsx` and `RunTab.tsx` in the O1 application to explain and expose the new functionality.

The automated tests check that the application's description and the pipeline's contents remain consistent.

### Embedding objective applications on the landing page

The landing page can display Windmill applications directly.

To make an objective application available there, set `public: true` in its `raw_app.yaml`.

Windmill Community Edition assigns each shared application an ID, but this ID differs between installations. We therefore avoid hardcoding application URLs.

Instead, the local bootstrap and `server/update.sh` discover the application's share ID and write it into an ignored file:

`runtime/<objective>.json`

The landing page reads this file to find the correct application.

When adding a new objective application, update both the local bootstrap and server update scripts to generate its runtime configuration.

## 5. How changes reach the server

GitHub manages the project's code, while GitHub Actions handles testing and deployment.

The aim is to make collaboration straightforward without allowing one person's changes to overwrite another's.

### The normal GitHub workflow

1. Create a branch for your changes.
2. Make your changes and open a pull request.
3. GitHub Actions checks the code.
4. Another team member reviews the pull request.
5. Once the checks pass and the changes are approved, merge it into `main`.

The checks include:

- Running tests and linting.
- Checking that Windmill can load the scripts, flows and applications.
- Validating the server configuration.

Merging into `main` starts the deployment workflow, visible under GitHub's **Actions** tab.

Within a couple of minutes, the updated `f/dhr` code is deployed to Windmill.

Every deployed script keeps a record of the commit it came from. Pipelines also record the commit alongside their stored results. This allows us to identify exactly which version of the code produced an output.

### Editing scripts directly in Windmill

Windmill's web editor is useful for developing and testing scripts without leaving the browser.

Changes to `f/dhr` made in Windmill take effect immediately, but they must also be brought back into GitHub.

A GitHub Actions workflow called **windmill to github** checks for these edits approximately every 15 minutes.

When it finds changes, it opens a pull request named **Changes made in Windmill**.

That pull request goes through the same review and testing process as any other change.

To trigger synchronisation sooner, open:

**GitHub Actions > windmill to github > Run workflow**

There are a few rules to keep in mind:

- **Windmill changes are protected.** If Windmill contains unmerged edits, GitHub deployment stops rather than overwriting them. The deployment reports why it stopped. Once the changes are merged, deployment resumes using everything on `main`.
- **Do not edit `windmill-changes`.** This branch is automatically rebuilt from Windmill approximately every 15 minutes. Make further changes in Windmill instead.
- **Check for conflicting edits.** If the same file has changed on GitHub since the previous deployment, the pull request reports the conflict. Review both versions so neither set of changes is accidentally lost.
- **Do not edit `windmill-snapshot`.** This branch records what Windmill contained after the previous deployment. The synchronisation script (`.github/windmill_sync.py`) uses it to detect new edits.

This arrangement allows the team to work in either GitHub or Windmill while keeping GitHub as the reviewed record of project changes.

## 6. Writing pipeline scripts

A Windmill pipeline script is an ordinary Python file stored in `f/dhr`.

To ensure scripts work consistently on local machines and the shared server, they must follow a few conventions. The automated checks enforce these rules.

### Entry point and arguments

Windmill executes a function named `main`.

Its arguments become the input fields displayed in Windmill, so every argument should have a type annotation and a simple default value.

For example:

```python
def main(revision: str = "main"):
    ...
```

Use a literal default rather than referring to a constant defined elsewhere.

The value returned by `main` becomes the result displayed in Windmill.

If a script cannot complete successfully, raise an exception with a clear message explaining the failure. Windmill will record the run as failed and display the error.

### Python dependencies

The project uses `uv` to manage Python dependencies.

The repository's `uv.lock` records exact package versions so local development and deployed scripts use consistent environments.

Windmill scripts also declare their dependencies in a metadata block at the top of the file:

```python
# /// script
# ...
# ///
```

The versions in this block must match those in `uv.lock`.

To add a dependency, first run:

```bash
uv add <package>
```

Then find its pinned version in `uv.lock` and add the same version to the script's metadata block.

This prevents differences between environments from affecting the results.

### Importing shared code

Reusable code should be placed in separate Python modules without a `main` function.

Within `f/dhr`, import shared functions explicitly:

```python
from .db import connect
```

Avoid module-level relative imports such as:

```python
from . import db
```

Windmill does not resolve that form correctly.

### Secrets and configuration

Never put passwords, API tokens or other secrets directly into code.

Use `settings.py`, which reads configuration from:

- Windmill's secret variables when running on the platform.
- Environment variables when running locally.

This keeps credentials outside the repository and makes it possible to run the same code in different environments.

### Database migrations

Changes to the database structure are managed in `f/dhr/migrations.py`.

Each migration is a numbered change, such as `005_template_matches`.

To add a table, column or other schema change, append a new migration to the file.

**Never modify a migration that has already been merged.** Earlier migrations may already have been applied to existing databases. Editing them would make those databases inconsistent.

Instead, add another migration containing the new changes.

Pipelines apply outstanding migrations automatically when they run.

## 7. Developing on your laptop

You do not need to run the entire Docker stack to develop Python pipelines.

For most changes, installing the project's Python environment is enough to run tests and work on scripts.

### Setting up Python

1. [Install uv](https://docs.astral.sh/uv/getting-started/installation/).
2. Clone the repository and install its dependencies:

   ```bash
   git clone https://github.com/NarraTrace-4YP/4yp_platform.git
   cd 4yp_platform
   uv sync
   ```

3. Run the tests:

   ```bash
   uv run pytest
   ```

If you need to run Windmill, MLflow, PostgreSQL and the landing page locally as well, follow the Docker setup in Section 2.

### Running scripts locally

Python scripts can be executed directly without using the Windmill interface.

For example, to test the case metadata collector:

```bash
uv run python -m f.dhr.collect_case_metadata --max-filters 10 --dry-run
```

This is useful for debugging and testing individual pipeline stages.

Scripts can also connect to the shared database through an SSH tunnel, provided `DHR_DATABASE_URL` is configured.

### Connecting to the shared database

PostgreSQL is not exposed directly to the internet. To access it from your laptop, create an SSH tunnel.

Run the following command and leave the terminal open:

```bash
ssh -N -L 5432:127.0.0.1:5432 <your-username>@<server-address>
```

This forwards connections to port 5432 on your computer to PostgreSQL on the shared server.

You can then use DBeaver, VS Code, `psql` or another PostgreSQL client with these settings:

| Setting | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `dhr` |
| Username | Your server username |
| Password | From `ACCOUNTS.txt` |

To connect from a Python script, set:

```text
DHR_DATABASE_URL=postgresql://<your-username>:<password>@localhost:5432/dhr
```

On the shared server itself, simply run `psql`.

### Database organisation

The database separates project data into three PostgreSQL schemas:

| Schema | Contents |
|---|---|
| `corpus` | Source reviews, processed sections and information derived from them. The wiki agent can read this. |
| `evaluation` | Case metadata, reference answers and other information reserved for evaluation. |
| `scratch` | Temporary tables, experiments and intermediate work. |

The separation between `corpus` and `evaluation` is particularly important.

The wiki agent must not have access to the evaluation schema. Otherwise, it could use information from the reference answers when building the wiki, making later evaluations unreliable.

Keeping evaluation data separate allows us to test what the wiki has learned from the review content rather than information supplied through the evaluation process.

### Logging experiments to MLflow

Scripts running on your laptop can log their results to the shared MLflow server.

Set the following environment variables before running them. For Bash:

```bash
export MLFLOW_TRACKING_URI="https://mlflow.<domain>"
export MLFLOW_TRACKING_USERNAME="<your-username>"
export MLFLOW_TRACKING_PASSWORD="<your-password>"
```

Use your server credentials from `ACCOUNTS.txt`.

This allows locally run experiments to appear alongside experiments executed through Windmill.

## 8. The datasets and processing pipeline

The project uses two collections of DHRs. They are stored separately because they have different levels of validation.

### The two datasets

**DHRC-100** contains 100 reviews whose document structure has been manually checked. This includes their headings, heading levels and relationships between sections.

**DHRC-RAW** contains 702 reviews processed automatically, without manual validation. It includes raw versions of the same 100 reviews found in DHRC-100, under the same names.

These are not 802 distinct reviews. The datasets overlap, but the validated and unvalidated versions are kept separate so we can compare them and track their provenance.

Both datasets are defined in `f/dhr/datasets.py`.

### Loading the reviews

The `f/dhr/load_dhrc` script imports reviews into PostgreSQL.

Select the dataset using the script's Windmill input form.

The loader:

1. Downloads the dataset from Hugging Face.
2. Checks each file against its recorded checksum to verify its integrity.
3. Stores the original JSON in `corpus.documents` without modifying it.
4. Records the dataset and version from which each review came.

Validation failures are handled differently depending on the dataset.

For DHRC-100, a review that cannot be read as a document causes the load to fail. Because this dataset has been manually validated, an unreadable document indicates a problem that needs investigating.

For DHRC-RAW, unreadable reviews are skipped and recorded in the logs. This allows the remaining reviews to load despite problems in the automatically generated data.

The latest version of each dataset is available through:

`corpus.current_documents`

Use the `dataset` column to select one dataset:

```sql
SELECT *
FROM corpus.current_documents
WHERE dataset = 'DHRC-100';
```

The original document JSON is stored in the `raw` column.

### Grouping text into sections

The source reviews are stored as page-based JSON, but pages do not necessarily correspond to meaningful sections.

For example, a section might begin halfway down one page and continue across several more. Analysing those pages independently would separate text that belongs together.

The `f/dhr/group_sections` script reconstructs each review's document hierarchy.

It identifies which headings and sections each text block belongs to, then places the blocks in reading order. This produces a structure that follows the content of the report rather than its page boundaries.

The source of the hierarchy depends on the dataset:

- **DHRC-100:** Uses the headings, levels and relationships checked by the team.
- **DHRC-RAW:** Uses the automatically extracted headings supplied by Datalab, which have not been manually checked.

Run this step after loading a dataset. Reloading a dataset removes its previous section grouping, so it must be regenerated.

The results are available through two database views:

| View | Contents |
|---|---|
| `corpus.current_sections` | One row per section, including its heading, level, parent section and page references. |
| `corpus.current_blocks` | One row per text block, including its section, page, HTML and plain text, in reading order. |

Page headers and footers are excluded.

Two database functions make it easier to retrieve a section, including content nested inside its subsections:

```sql
-- Retrieve the section's contents block by block
SELECT *
FROM corpus.section_blocks(
    'DHRC-100',
    '<doc_id>',
    '<section_id>'
);

-- Retrieve the same contents as one text string
SELECT corpus.section_text(
    'DHRC-100',
    '<doc_id>',
    '<section_id>'
);
```

To inspect the structure of a single review without changing the database, run:

```bash
uv run python -m f.dhr.group_sections --file <review.json>
```

This prints the review's reconstructed outline.

### Finding matches to a standard report template

Different DHRs use different headings and report structures. This makes direct comparison difficult because similar information might appear under different section names.

To address this, the project maps review sections to a common structure based on the Home Office's statutory guidance.

Rather than asking an LLM to choose from every possible template section, the pipeline first identifies a short list of likely matches. This reduces the amount of information the LLM needs to consider and allows us to use more expensive inference only where it is needed.

The `f/dhr/find_template_candidates` script produces these candidate lists.

It supports two sets of guidance:

- **2016 DHR guidance:** The overview report, executive summary, Individual Management Reviews (IMRs) and action plan templates. This is the default used in the project specification.
- **September 2026 DARDR guidance:** The DARDR template from its Word document, together with the action plan, scoping review and IMR templates from the guidance PDF.

The templates are downloaded from GOV.UK and parsed each time the script runs. The SHA-256 hash of each downloaded file is stored so we know exactly which source document was used.

Candidate selection uses two complementary methods:

1. **Heading similarity:** Compares the review's heading with template headings using fuzzy word matching. Section numbering is removed first, so differences such as "3.2 Findings" and "Findings" do not prevent a match.
2. **Content similarity:** Compares the text of the section with the guidance describing each template section. It uses embeddings generated by the small open-weight model `BAAI/bge-small-en-v1.5`, which runs on the server's CPU.

Heading matching works well when reports use familiar names. Content similarity helps when a section has an unusual heading but discusses material covered by a particular part of the template.

The script combines candidates identified by these methods into a shortlist for each review section.

Typical runtimes are approximately:

- **DHRC-100:** 5 minutes.
- **DHRC-RAW:** 40 minutes.

Run candidate generation after section grouping. Regrouping a dataset removes its old candidates, so they must be regenerated.

Candidates are stored separately for each dataset and guidance version.

To inspect the candidates for a review:

```sql
SELECT
    section_heading,
    template_title,
    template_heading,
    heading_score,
    text_score
FROM corpus.current_template_candidates
WHERE dataset = 'DHRC-100'
  AND guidance = 'dhr-2016'
  AND doc_id = '<doc_id>'
ORDER BY section_position, text_rank;
```

**The next planned step is LLM-based matching.** The model will examine each section and its shortlist, then choose the most appropriate template section or indicate that none is suitable.

This will produce the standardised representation needed for later analysis.

### Collecting Home Office case metadata

The Home Office DHR Library provides structured information about cases, including details such as the victim's age, relationship to the perpetrator and method of killing.

This information is useful for understanding the datasets and evaluating the project's outputs.

However, the library does not provide a bulk download of these records.

The `f/dhr/collect_case_metadata` script therefore queries the library using its available search filters. By recording which reviews match each filter, it reconstructs the available case attributes.

For example, the script can identify reviews associated with a particular age range or relationship category without manually opening every record.

It processes the whole Home Office library, so one run collects the information needed for both datasets.

The results are stored in the `evaluation` schema, where the wiki agent cannot access them.

Two tables provide access:

| Table | Contents |
|---|---|
| `evaluation.case_metadata` | One row per review per dataset, with the available case details stored as JSON. |
| `evaluation.case_attributes` | One row per individual case attribute. |

Both tables include a `dataset` column.

There are two important limitations to remember:

**Missing attributes are ambiguous.** If an attribute is absent, it means the library did not return it. This could mean the answer is "no", or that the information was not recorded. The pipeline does not treat these as equivalent.

**Not every review originates from the Home Office website.** Reviews obtained through HALT or other sources may not have corresponding Home Office case details. The `source` column records whether a review came from `Home Office website`, `HALT` or `Elsewhere`.

The library data is published under the [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) and has been checked by people, although the Home Office notes that errors may remain.

The separation of this metadata from the wiki-building process is deliberate. We want the wiki to be built from the review text, then independently evaluate whether it captures the information available from other sources.

## 9. The LLM wiki

The wiki is the project's main knowledge representation.

Instead of treating every review as an isolated document, the wiki agent will organise information into linked Markdown pages describing people, agencies, events and recommendations.

These pages will include citations to the original review passages so that claims can be checked against their sources.

The aim is to make information spread across different parts of a review easier to find, follow and analyse.

### Storage and version history

The wiki is stored as Markdown files in a separate, private GitHub repository.

This keeps the generated content independent of the platform's code.

The wiki agent commits its changes to Git, preserving a history of what it wrote. This provides a record of how the wiki developed and allows earlier versions to be inspected.

### Reading the wiki

The Markdown files can be opened as an Obsidian vault.

Obsidian displays links between pages and provides a convenient way to navigate the information without needing a custom application.

The **Obsidian Git** community plugin can keep a local copy synchronised with the wiki repository.

This makes the wiki accessible for manual review alongside automated analysis.

## 10. Repository structure

The repository is organised by the purpose of each component.

| Path | Contents |
|---|---|
| `f/dhr/` | Team pipelines, Windmill scripts and shared Python modules. |
| `tests/` | Automated tests for pipeline code and the website. |
| `site/` | Project landing page and about page. |
| `wmill.yaml` | Windmill workspace deployment configuration. |
| `.github/workflows/deploy.yml` | GitHub Actions workflow for deploying `main` to Windmill. |
| `server/` | Server provisioning and maintenance scripts. |
| `postgres/` | PostgreSQL configuration. |
| `mlflow/` | MLflow configuration. |
| `caddy/` | Web server and reverse proxy configuration. |
| `compose.yaml` | Docker Compose definition for the platform's services. |

### The landing page

The landing page is served at:

`https://<domain>`

It provides links to the project's services, documentation and objective applications.

It uses plain HTML and CSS with the NarraTrace branding, without a separate build process.

Unlike Windmill scripts, changes to `site/` are not published automatically when a pull request is merged.

An admin must update the server by running:

```bash
sudo /opt/dhr/server/update.sh
```

### Server configuration

Most team members should only need to work in `f/dhr/`, `tests/` and occasionally `site/`.

The `server/`, `postgres/`, `mlflow/`, `caddy/` and Docker Compose configuration files control the shared infrastructure and should normally be changed by admins.

### Data and secrets

**Never commit datasets, passwords or API tokens to GitHub.**

The source datasets are distributed through Hugging Face and loaded into PostgreSQL by the pipelines.

Credentials are managed through the platform's environment settings and secret variables.

Keeping these outside the repository prevents accidental disclosure and avoids storing large datasets alongside code.

## 11. Blythe integration

GPU access is the main part of the platform that is not yet configured.

The plan is to use Blythe for inference with open-weight LLMs while continuing to coordinate jobs from Windmill on the shared server.

One possible approach is for a Windmill script to submit SLURM jobs to Blythe over SSH. However, before using this approach, we need to confirm with the university's HPC team (SCRTP) whether automated SSH connections from an external server are permitted.

An HTTP-based job interface is another possibility being discussed.

Until an approved connection method is available, the Blythe integration remains separate from the working server platform.

---

## Where to start

If you're new to the project, the simplest route is:

1. **Set up the Python environment** using Section 7, so you can run tests and develop scripts.
2. **Get access to the shared server** using Section 3.
3. **Explore the O1 application** in Windmill to understand the datasets and processing pipeline.
4. **Run an existing script** to see how inputs, logs and results work.
5. **Develop in your personal Windmill folder or a GitHub branch**, then submit your work through a pull request.

For local infrastructure development, follow the [full local setup guide](docs/local-setup.md). For server administration, use the [server setup guide](docs/server-setup.md).
