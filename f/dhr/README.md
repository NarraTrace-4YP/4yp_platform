# DHR code layout

Objective-specific code lives under its objective folder. Shared database connections,
dataset definitions, migrations, settings, and code-version information stay in `f/dhr`.
Windmill secrets also keep their existing `f/dhr/<name>` paths.

## O1: corpus preparation

The stage folders follow the names shown in the O1 app. Each stage contains its runnable
script, stage-specific helpers, and any read-only scripts used to display that stage's
results. Python files use descriptive `snake_case` names; React components use PascalCase.

```text
O1/
  load_reviews/
    load_reviews.py
  link_case_metadata/
    link_case_metadata.py
    get_reviews.py
    get_case_details.py
    get_attribute_counts.py
  group_blocks_by_section/
    group_blocks_by_section.py
    get_sections.py
  find_candidate_template_sections/
    find_candidate_template_sections.py
    templates.py
    embeddings.py
    get_candidates.py
  choose_template_section/
    choose_template_section.py   # a workflow: runs the LLM on your own Blythe account
    prompts.py
    get_choices.py
  app_backend/
    get_status.py
  app.raw_app/                   # React frontend and Windmill backend bindings
  pipeline.flow/flow.yaml        # orchestration of the implemented stages
```

`blythe.py` (shared) logs in to Blythe and runs the fixed SLURM jobs from the
separate `NarraTrace-4YP/narratrace-slurm` repository. Every run logs in as the person who
started it, with their own Blythe account, read from their own Windmill variables:

| Variable                        | What it holds                                                |
| ------------------------------- | ------------------------------------------------------------ |
| `u/<you>/blythe_username`       | your Blythe login (Rowan's is known, so his can be left out) |
| `u/<you>/blythe_ssh_key`        | the private key SCRTP allowed on the project server (secret) |
| `u/<you>/blythe_key_passphrase` | its passphrase (secret)                                      |
| `u/<you>/blythe_totp_secret`    | your 2FA secret, or the otpauth:// link it came in (secret)  |

Make them in Windmill (Resources > Variables, under your own `u/<you>/` folder, ticking
"secret"). Only your runs, and workspace admins, can read them. Before your first run: SCRTP
must agree to your key and 2FA being used from the project server, as they did for Rowan,
and `~/narratrace` must be set up in your Blythe account (narratrace-slurm's README). A run
whose person has no account set up stops before contacting Blythe. To check your set-up,
run `tools/blythe_check` in Windmill: it tests your variables, the login and your
`~/narratrace`, and says how to fix whatever isn't ready (it changes nothing on Blythe). Because stage 5 needs a
Blythe account, it is not a step of `f/dhr/O1/pipeline`: run `choose_template_section` on its
own after stage 4. See its README.

`tools/llm_server.py` (on your own Blythe account, like everything here) starts narratrace-slurm's
`llm_serve` job on Blythe, which keeps a model loaded and answers prompts added while it
runs: start it, add prompts, look at its status, collect a request's answers, and stop it.
The mailbox functions it uses (`start_server`, `add_request`, `mailbox`, `collect`,
`stop_server`, `events`) are in `blythe.py`, for pipelines to use too.

For code that just wants to ask the model (O2's wiki agent, say), use the LLM gateway
instead: `llm.py` gives an `openai` client or a LangChain `ChatOpenAI` that runs on your own
Blythe account, starting your model job when needed (`gateway/README.md`).
`tools/llm_chat.py` asks it one question from Windmill, to try it.

The complete flow is `f/dhr/O1/pipeline`; the app is `f/dhr/O1/app`. For example, the
loading script's Windmill path is `f/dhr/O1/load_reviews/load_reviews`. These replace the
previous flat paths. Deployment, bootstrap, app bindings, and source links use the new
paths; `/site/o1/` continues to load its instance-specific public URL from `/runtime/o1.json`.
Existing saved links to flat script paths need updating after deployment. Flow step IDs
and input field names are kept stable because the app uses them to follow job progress.

Run a stage locally as a module, for example:

```sh
uv run python -m f.dhr.O1.load_reviews.load_reviews --dataset DHRC-100
uv run python -m f.dhr.O1.group_blocks_by_section.group_blocks_by_section --file review.json
```

Loading replaces a revision's documents and invalidates its section grouping and
candidates. Grouping replaces the hierarchy and invalidates candidates. Candidate matching
replaces only the selected guidance edition's matches. Choosing template sections replaces
the choices for that dataset version and guidance edition. Metadata collection is independent:
one DHR Library snapshot can link reviews from both datasets.

## Editing conventions

- Start each module with a short description of its purpose, inputs, outputs, and relevant
  failure or replacement behaviour. Keep Windmill's dependency block before that docstring.
- Explain constraints and decisions beside the relevant algorithm. Avoid historical fix
  diaries, comments that repeat the next statement, and comments that promise future code.
- Use explicit relative imports: `from .templates import ...` for a sibling helper and
  `from ...db import connect` for shared infrastructure from a stage or `app_backend` folder.
- Preserve `main` parameter names, literal defaults, response keys, database column names,
  and existing migration SQL when performing a readability refactor.
- Keep backend YAML names in sync with `backend` and `backendAsync` frontend calls. After
  moving a script, update bindings, flow paths, run-status permissions, docs, and tests.

Python uses Ruff with a 100-character line length. React, CSS, JavaScript, HTML, JSON,
and app/flow YAML use the pinned Prettier version and the root configuration. The static
site in `/site` follows the same frontend formatting conventions.

```sh
uv run ruff format f/dhr
uv run ruff check .
npm ci
npm run format
npm run format:check
uv run pytest
```

Generated Windmill dependency metadata and build output remain ignored. Do not edit
existing database migrations to reflect script renames; their original SQL is immutable.
