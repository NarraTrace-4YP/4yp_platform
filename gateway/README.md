# LLM gateway

An OpenAI-style API to the team's model on Blythe. Code calls it like any OpenAI-compatible
service (the `openai` client, or LangChain's `ChatOpenAI`), and the gateway does the rest:
it logs in to Blythe **as the caller**, starts their model job if it isn't running, sends
the question, and returns the answer. Nobody's code needs to know about SLURM, 2FA or run
folders.

```text
your code ──HTTP──> llm-gateway (project server) ──SSH, as you──> Blythe: your llm_serve job (GPU)
          <──────── OpenAI-style answer <───────── inbox/ outbox/ files <─────────
```

## Using it

You need your own Blythe account set up first (`f/dhr/README.md`: your `u/<you>/blythe_*`
variables, SCRTP's agreement, and `~/narratrace` on Blythe).

**In a Windmill script**, use the helper in `f/dhr/llm.py`. It finds the gateway and uses the
run's own token, so the work runs on the Blythe account of whoever runs the script:

```python
from ..llm import MODEL, chat_model, client  # adjust the dots to your folder depth

reply = client().chat.completions.create(
    model=MODEL, messages=[{"role": "user", "content": "Summarise this section: ..."}]
)
print(reply.choices[0].message.content)

agent_model = chat_model(temperature=0)  # LangChain ChatOpenAI, e.g. for LangGraph
```

Pin `openai` (for `client`) or `langchain-openai` (for `chat_model`) in the script's
dependency block, at the versions in `uv.lock`. To try it without writing anything, run
`f/dhr/tools/llm_chat` in Windmill.

**From your laptop**, open a tunnel and use a Windmill token you made (Windmill: Account
settings > Tokens):

```sh
ssh -N -L 8100:127.0.0.1:8100 <you>@windmill.rshields.xyz
export NARRATRACE_LLM_URL=http://127.0.0.1:8100/v1 WINDMILL_TOKEN=<your token>
```

Then `f/dhr/llm.py`'s `client()` and `chat_model()` work as they do in Windmill.

## What it supports

`POST /v1/chat/completions`, with:

- chat messages (system, user, assistant, tool; `developer` is treated as system);
- `max_tokens` / `max_completion_tokens` (default 1,024), `temperature`, `top_p`, `stop`, `seed`;
- `response_format`: `json_object`, or `json_schema` (the answer always parses);
- `tools` and `tool_choice` `auto` or `none`: Qwen's tool calls come back as OpenAI
  `tool_calls`, so LangChain's `bind_tools` and LangGraph agents work;
- two extras (`extra_body=` in the openai client): `"labels": ["A", "B"]` answers with one
  label and gives each label's probability (`message.label_probabilities`), like stage 5's
  questions; `"thinking": true` lets Qwen think first (`message.reasoning_content`).

Refused rather than ignored: streaming, `n` above 1, log-probabilities, frequency/presence
penalties, images, and forcing a particular tool. `GET /v1/models` names the model;
`GET /status` shows your job and waiting calls; `GET /usage` your calls and tokens so far.

## What to expect

- **The first call after a quiet spell waits for a GPU and for the model to load**: about 3
  minutes when Blythe is quiet, longer when its queue is busy. The helper waits up to an hour.
  Your job then stays up while calls keep coming, and stops after 30 minutes with nothing to
  do, so it doesn't hold a shared GPU.
- **After that, each call adds about a second** to the model's own time: the files go
  through your held-open login, and the job checks its inbox twice a second.
- **Calls arriving together are answered together.** Calls within 0.1 s of each other go to
  the GPU as one batch, so many calls at once (e.g. from a thread pool) is the fast way.
- **Each person has their own job.** Two people working at once use two GPUs, each on their
  own account.

## How it works

- `app.py`: the web app. The API key is a Windmill token; the gateway asks Windmill who it
  belongs to, then reads that person's own Blythe variables with the same token (Windmill
  only lets a token read its owner's variables). It only ever acts as the token's owner.
- `sessions.py`: one session per person: their Blythe login (one 2FA code, kept open with
  keep-alives), their `llm_serve` job, batching, and reading the job's `events.log`. A job
  that ends with calls waiting (time limit, cancelled) is replaced and the calls sent again.
- `openai_format.py`: OpenAI requests to `llm_serve` prompts and back, including reading
  Qwen's `<tool_call>` blocks.
- The job is narratrace-slurm's `llm_serve` (its README); the Blythe commands are the fixed
  ones in `f/dhr/blythe.py`. Usage is recorded in the `llm-gateway-data` volume.

It runs as the `llm-gateway` service in `compose.yaml`, reachable only inside the server
(`http://llm-gateway:8000/v1` from Windmill scripts, `127.0.0.1:8100` on the server for
tunnels). It is never put on the public website. After merging a change, update the
server with `sudo /opt/dhr/server/update.sh`, which rebuilds the image.

Settings can be changed with environment variables on the service, e.g.
`GATEWAY_IDLE_MINUTES=45` or `GATEWAY_DEFAULT_MODEL=qwen-27b-fp8` (every field of `Settings`
in `sessions.py`, as `GATEWAY_<NAME>`).

## Models

The gateway serves several models, listed once, in `MODELS` in `sessions.py`, by key. A call
chooses one with OpenAI's `model` field: its key, its Hugging Face name, or `default`
(`GATEWAY_DEFAULT_MODEL`, set in `.env`; `qwen-27b-bf16` unless changed). `GET /v1/models`
lists them with what a WeKnora entry needs (label, context window, concurrency). Each person
gets one Blythe job per model they use, so two builds on different models run side by side.

| Key | Model | GPUs | Prompt limit | At once |
| --- | --- | --- | --- | --- |
| `qwen-27b-bf16` (default) | Qwen/Qwen3.8-27B, full precision | 2 | 131,072 | 64 |
| `qwen-27b-bf16-graphs` | the same, with CUDA graphs (untested) | 2 | 131,072 | 64 |
| `qwen-27b-fp8` | Qwen/Qwen3.8-27B-FP8, 8-bit | 1 | 32,768 | 128 |

The two-GPU models use the settings of the first two-GPU test job that worked (1389794,
25 Nov 2026): NCCL for every all-reduce, no direct GPU-to-GPU copies (the job file sets that),
and a 20-second check that the GPUs can talk before the model loads. `qwen-27b-bf16` also
skips CUDA graphs (`enforce_eager`); `qwen-27b-bf16-graphs` doesn't, so it is faster per token
but takes a few more minutes to start (compiled again in every job). Loading takes about 5
minutes once a job has its GPUs; two GPUs may queue longer than one.

**Adding a model:** add an entry to `MODELS` (and its key to the O2 flow's `model` choices),
download it on Blythe with `slurm/jobs/setup_env.sbatch <Hugging Face name>` for each
person, merge and run `update.sh`. Nobody edits WeKnora: `f/dhr/O2/check_setup` makes each
person's WeKnora entry for a model the first time they build with it (`docs/weknora.md`).

## Waiting days for GPUs

A two-GPU job can wait in SLURM's queue for hours or days. Rather than holding one call open
that long, a flow asks the gateway to submit the job and then checks back:

- `POST /start?model=<key>`: submits your job for that model if there is none (or it has
  ended) and returns at once.
- `GET /status?model=<key>&refresh=true`: asks Blythe how it is doing: `job_state` (llm_serve's own state,
  empty while queued), `slurm` (e.g. `PENDING`, reason `Resources`) and `last_failure`.

`f/dhr/llm.py` has `gateway_start()` and `gateway_refresh()`; the O2 flow's `start_model` and
`wait_for_model` steps use them, sleeping between checks without holding a Windmill worker.
