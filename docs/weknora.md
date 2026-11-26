# WeKnora

[WeKnora](https://github.com/Tencent/WeKnora) is a knowledge-base website. Its wiki mode
reads the documents you upload and writes linked pages about the people, organisations,
concepts and events in them, with a summary page for each document and a graph of how they
connect. Every page cites the parts of the reviews it came from.

On the server it lives at `https://weknora.<domain>`.

## Logging in

`server/team.sh sync` gives everyone in `server/team.txt` a WeKnora login. Look in
`~/ACCOUNTS.txt` on the server:

- The email is the same as your Windmill one.
- The password is your usual one, unless the file gives a separate WeKnora password. WeKnora
  needs at least one letter and one number in a password, and a few of the generated ones
  had no number.

You can't sign up on the website itself. The Caddyfile blocks the sign-up API, because
anyone with an account could spend the team's GPU time. To add someone, put them in
`team.txt` and run `sudo /opt/dhr/server/team.sh sync`. `team.sh remove` also locks their
WeKnora login.

The first admin in `team.txt` is WeKnora's system admin. They can reset passwords and
disable accounts from WeKnora's own admin pages.

## Setting up the model (once per person)

WeKnora calls the model through the LLM gateway (`gateway/README.md`), using your own
Windmill token. Each call then runs on your own Blythe account, so first make sure your
Blythe variables are set up and `f/dhr/tools/blythe_check` passes.

In the team space's model settings, add ONE chat model by hand:

| Setting      | Value                                                      |
| ------------ | ---------------------------------------------------------- |
| Provider     | Custom (OpenAI-compatible)                                 |
| Base URL     | `http://llm-gateway:8000/v1`                               |
| API key      | your Windmill token (Windmill → Account settings → Tokens) |
| Model name   | `default` (any name works: the gateway's default answers)  |
| Display name | `Qwen (<your Windmill username>)`                          |

That entry is the template for the rest. The first time you build a wiki with a model, the
O2 pipeline's `check_setup` copies it (so your token comes along) into an entry for that
model: model name = the gateway's key (e.g. `qwen-27b-bf16`), display name e.g.
`Qwen3.8 27B BF16 (<you>)`, with the model's context window and background concurrency.
No model names need typing into WeKnora, and choosing a model is the pipeline's `model`
input (the gateway's list: `gateway/README.md`, "Models").

Start the model first (run `f/dhr/tools/llm_chat` and wait for an answer). Its first
request otherwise waits for SLURM to give it a GPU and for the model to load. That takes
minutes, and WeKnora's "test connection" button gives up after 30 seconds. If the test still
times out, save the model anyway: the wiki is built in the background, which waits much
longer.

## Making a wiki

The O2 pipeline in Windmill (`f/dhr/O2/pipeline`) does all of this for you, with the
settings in `f/dhr/O2/kb_settings.py`. By hand:

1. Create a knowledge base and pick the model above as its chat model.
2. In its indexing settings, turn **Wiki** on and the vector and keyword indexes off. The
   gateway has no embedding model, and the wiki doesn't need one.
3. Upload the reviews (Markdown works best). Each one is read, then the wiki is built in
   the background. The Wiki and Graph tabs fill in as it goes.

A knowledge base belongs to the space it was made in. Everyone starts with a personal
space. To share one with the team, invite them to your space from its members page.

Each review is uploaded whole, as one Markdown file. WeKnora's wiki builder reads only the
first 32,768 characters of each file, so for most DHRs the wiki is built from the start of
the review; `check_setup` reports how many reviews that affects
(`n_longer_than_wiki_reads`).

## Deleting a wiki

Run `f/dhr/O2/delete_wiki` in Windmill with the build's `instance_id` (from the O2 app's
Wikis tab) as both `instance_id` and `confirm`. It stops the build's pipeline run if it is
still going, deletes the knowledge base in WeKnora (files, chunks, wiki pages and graph),
removes its entities from the graph database, and deletes the build's copy from the
database, so it disappears from the O2 app. Only the person who made a build (or a
Windmill workspace admin) can delete it,
and the public O2 app can't delete anything. Deleting a knowledge base in WeKnora's own
pages instead leaves the build in the O2 app.

## Knowledge graph

Besides the wiki, WeKnora can build a knowledge graph: for every chunk of every review, the
model lists the people, agencies, places and events in it, and the relationships between
them. This is different from the wiki's page-link graph (the Graph tab in the O2 app shows
the links between wiki pages). The O2 pipeline turns it on for every build (input
`knowledge_graph`, default on). The relationship types and the worked example the model
learns from are in `f/dhr/O2/graph.py`.

It needs a graph database: the **weknora-neo4j** service. Without it, WeKnora's knowledge
base settings say "Knowledge graph database is not enabled" and the pipeline's first step
stops. To turn it on (once, on the server):

```bash
cd ~/4yp_platform            # wherever the repository is on the server
git pull
server/update.sh             # adds WEKNORA_GRAPH_PASSWORD to .env and starts weknora-neo4j
docker compose ps weknora-neo4j weknora-app     # both should say "healthy"
```

Then, in WeKnora, open any knowledge base's settings: the knowledge graph section should no
longer show the warning, and Settings → System Info should list the graph database as Neo4j.
Graphs made in WeKnora's own website also need their relationship types and example filled
in there; the pipeline fills them in itself.

Each check of a build also copies its graph from Neo4j into the project database
(`wiki.kg_nodes`, `wiki.kg_relations`), and the O2 app's **Knowledge graph** tab shows it in
3D. Windmill reads Neo4j with the password in the secret variable
`f/dhr/weknora_graph_password`, which `server/update.sh` copies from `.env`. Until then the
checks say "No Neo4j password" in their result and carry on without the graph.

The graph costs a model call per chunk, on top of the wiki's. At WeKnora's default
`chunk_size` of 512 characters that is about 250 calls for a typical review, so a larger
`chunk_size` (2,000, say) makes it about four times cheaper. The pipeline's first step
(`check_setup`) estimates the calls before anything starts.

## "An error occurred" (nginx) in WeKnora

If WeKnora's pages show nginx's "An error occurred. Sorry, the page you are looking for is
currently unavailable", the website can't reach `weknora-app`. Usually `weknora-app` was
recreated and got a new address on the Docker network, and the website's web server still
has the old one (it looks it up only when it starts). Restart the website:

```bash
docker compose restart weknora-frontend
```

`compose.yaml` now restarts it by itself whenever `docker compose up` restarts `weknora-app`.

## Watching the model calls

WeKnora writes every model call it makes, in full, to its LLM debug log: the messages it
sent, the answer (or the error) and how long it took, one file per request, deleted after
7 days. On the server, from the platform folder:

```bash
docker compose exec weknora-app sh -c 'ls -t /data/files/llm_debug | head'             # newest first
docker compose exec weknora-app sh -c 'tail -f "/data/files/llm_debug/$(ls -t /data/files/llm_debug | head -1)"'
docker compose exec weknora-app sh -c 'grep -l -i error /data/files/llm_debug/* | head' # calls that failed
```

The files hold review text, so they stay on the server. On Blythe,
`narratrace-slurm/watch_prompts.py` shows the same calls arriving at the model.

## Watching it work

On the server:

```bash
docker compose logs -f weknora-app       # what WeKnora is doing, and any errors
docker compose logs -f llm-gateway       # requests going to the model and replies coming back
```

## How it runs

Five services in `compose.yaml`, all reached only through Caddy:

- **weknora-frontend**: the website.
- **weknora-app**: the API and the background jobs that build wikis.
- **weknora-docreader**: turns uploaded files into text.
- **weknora-redis**: WeKnora's job queue.
- **weknora-neo4j**: the knowledge graph's database (no port opened on the server).

There is also **weknora-db-init**, which runs once at each start. It creates the
`weknora` database and login in the shared Postgres if they are missing, then exits.

WeKnora's data is the `weknora` database plus the uploaded files in the `weknora-files`
volume and the graph in the `weknora-neo4j-data` volume. Its secrets are the `WEKNORA_*` settings in `.env`. `server/weknora.sh env` fills
them in; `setup.sh` and `update.sh` run it.

Never change `WEKNORA_AES_KEY`. It encrypts the API keys saved in WeKnora, including
everyone's Windmill token, and those can't be read without it.

Together these services are limited to about 4 GB of memory (Neo4j's 1 GB included). Check `docker stats` after the
first big upload.

## Upgrading

Change the version (`v0.9.0`) on all three `wechatopenai/weknora-*` images in `compose.yaml`
together. Read WeKnora's release notes first, then run `server/update.sh`. WeKnora updates
its own database tables when it starts.

WeKnora's own set-up uses ParadeDB 0.25. Ours is 0.26, which is fine for the wiki. If
keyword search ever misbehaves, this version difference is the first thing to check.
