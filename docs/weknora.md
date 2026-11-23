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

In WeKnora's model settings, add a chat model:

| Setting  | Value                                       |
| -------- | ------------------------------------------- |
| Provider | Custom (OpenAI-compatible)                  |
| Base URL | `http://llm-gateway:8000/v1`                |
| API key  | your Windmill token (Windmill → Account settings → Tokens) |
| Model    | `Qwen/Qwen3.8-27B-FP8`                      |

Start the model first (run `f/dhr/tools/llm_chat` and wait for an answer). Its first
request otherwise waits for SLURM to give it a GPU and for the model to load. That takes
minutes, and WeKnora's "test connection" button gives up after 30 seconds. If the test still
times out, save the model anyway: the wiki is built in the background, which waits much
longer.

## Making a wiki

1. Create a knowledge base and pick the model above as its chat model.
2. In its indexing settings, turn **Wiki** on and the vector and keyword indexes off. The
   gateway has no embedding model, and the wiki doesn't need one.
3. Upload the reviews (Markdown works best). Each one is read, then the wiki is built in
   the background. The Wiki and Graph tabs fill in as it goes.

A knowledge base belongs to the space it was made in. Everyone starts with a personal
space. To share one with the team, invite them to your space from its members page.

## Watching it work

On the server:

```bash
docker compose logs -f weknora-app       # what WeKnora is doing, and any errors
docker compose logs -f llm-gateway       # requests going to the model and replies coming back
```

## How it runs

Four services in `compose.yaml`, all reached only through Caddy:

- **weknora-frontend**: the website.
- **weknora-app**: the API and the background jobs that build wikis.
- **weknora-docreader**: turns uploaded files into text.
- **weknora-redis**: WeKnora's job queue.

There is also **weknora-db-init**, which runs once at each start. It creates the
`weknora` database and login in the shared Postgres if they are missing, then exits.

WeKnora's data is the `weknora` database plus the uploaded files in the `weknora-files`
volume. Its secrets are the `WEKNORA_*` settings in `.env`. `server/weknora.sh env` fills
them in; `setup.sh` and `update.sh` run it.

Never change `WEKNORA_AES_KEY`. It encrypts the API keys saved in WeKnora, including
everyone's Windmill token, and those can't be read without it.

Together these services are limited to about 3 GB of memory. Check `docker stats` after the
first big upload.

## Upgrading

Change the version (`v0.9.0`) on all three `wechatopenai/weknora-*` images in `compose.yaml`
together. Read WeKnora's release notes first, then run `server/update.sh`. WeKnora updates
its own database tables when it starts.

WeKnora's own set-up uses ParadeDB 0.25. Ours is 0.26, which is fine for the wiki. If
keyword search ever misbehaves, this version difference is the first thing to check.
