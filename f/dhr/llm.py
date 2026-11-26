"""The team's LLM, through the LLM gateway: OpenAI-style clients that run on your own Blythe account.

Inside a Windmill script (the gateway knows you from the run's own token, WM_TOKEN):

    from .llm import MODEL, chat_model, client          # (from ..llm in a subfolder, etc.)

    reply = client().chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": "..."}]
    )
    agent_model = chat_model(temperature=0)             # LangChain, e.g. for LangGraph

The script's dependency block must pin openai (for client) or langchain-openai (for
chat_model) to the version in uv.lock.

From your laptop, open a tunnel to the server first and give a Windmill token you made
(Windmill: Account settings > Tokens):

    ssh -N -L 8100:127.0.0.1:8100 <you>@windmill.rshields.xyz
    NARRATRACE_LLM_URL=http://127.0.0.1:8100/v1  WINDMILL_TOKEN=<your token>

The first call after the model has been idle waits for a GPU and for the model to load
(minutes), so the clients wait up to an hour. gateway/README.md has the details.
"""

from __future__ import annotations

import os

# The model a call names when it doesn't choose one: the gateway's default (since 26 Nov 2026,
# qwen-27b-bf16). Choose another by its key, e.g. model="qwen-27b-fp8"; models() lists them
# (the catalogue is MODELS in gateway/sessions.py, the only place model names are written).
MODEL = "default"
GATEWAY_URL = "http://llm-gateway:8000/v1"  # inside the project server's Docker network
TIMEOUT_SECONDS = 3600


def connection() -> tuple[str, str]:
    """The gateway's address and your Windmill token."""
    url = os.environ.get("NARRATRACE_LLM_URL", GATEWAY_URL)
    token = os.environ.get("WM_TOKEN") or os.environ.get("WINDMILL_TOKEN")
    if not token:
        raise RuntimeError(
            "No Windmill token: inside Windmill WM_TOKEN is set for you; elsewhere set"
            " WINDMILL_TOKEN to a token made in Windmill (Account settings > Tokens)."
        )
    return url, token


def client(**options):
    """An openai.OpenAI client for the gateway."""
    from openai import OpenAI

    url, token = connection()
    options.setdefault("timeout", TIMEOUT_SECONDS)
    return OpenAI(base_url=url, api_key=token, **options)


def chat_model(**options):
    """A LangChain ChatOpenAI for the gateway (tools, structured output and LangGraph work)."""
    from langchain_openai import ChatOpenAI

    url, token = connection()
    return ChatOpenAI(model=MODEL, base_url=url, api_key=token, timeout=TIMEOUT_SECONDS, **options)


def models() -> dict:
    """The gateway's models: {"default": key, "data": [{"id": key, "label", "model" (its Hugging
    Face name), "context_window", "concurrency", "gpus"}, ...]}."""
    return _gateway("get", "/v1/models")


def model_info(model: str = MODEL) -> dict:
    """One model's entry from models(): `model` is its key, its Hugging Face name or
    "default". Raises with the choices if the gateway doesn't serve it."""
    listed = models()
    choices = {m["id"]: m for m in listed["data"]}
    if model in (None, "", "default"):
        return choices[listed["default"]]
    if model in choices:
        return choices[model]
    by_name = [m for m in listed["data"] if m["model"] == model]
    if by_name:
        default = choices[listed["default"]]
        return default if default in by_name else by_name[0]
    raise ValueError(
        f"The LLM gateway has no model {model!r}: choose one of {sorted(choices)} or"
        f' "default" ({listed["default"]}).'
    )


def gateway_status(model: str = MODEL) -> dict:
    """What the gateway knows of your model right now, without contacting Blythe: your job's
    state (SLURM job id, run folder), calls queued and with the job, and your calls and tokens
    so far. {"error": ...} if the gateway can't be asked."""
    import httpx

    url, token = connection()
    base = url.removesuffix("/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        status = httpx.get(
            f"{base}/status", params={"model": model}, headers=headers, timeout=10
        ).json()
        usage = httpx.get(f"{base}/usage", headers=headers, timeout=10).json()
    except (httpx.HTTPError, ValueError) as error:
        return {"error": f"can't ask the gateway: {error}"}
    return {**status, "usage": usage}


def gateway_start(model: str = MODEL) -> dict:
    """Submits your job for one model on Blythe if it isn't there (or has ended) and returns at
    once with its state: it may then wait in SLURM's queue for hours or days. Raises if the
    gateway or Blythe can't be asked."""
    return _gateway("post", "/start", {"model": model})


def gateway_refresh(model: str = MODEL) -> dict:
    """Your job for one model, asking Blythe first: job_state (llm_serve's: None while queued,
    then loading, waiting = ready, answering), slurm (SLURM's state and reason, e.g. PENDING /
    Resources) and last_failure (why the last job failed). Raises if it can't be asked."""
    return _gateway("get", "/status", {"model": model, "refresh": "true"})


def _gateway(method: str, path: str, params: dict | None = None) -> dict:
    import httpx

    url, token = connection()
    base = url.removesuffix("/v1").rstrip("/")
    reply = httpx.request(
        method,
        base + path,
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=180,
    )
    body = reply.json()
    if reply.status_code != 200:
        raise RuntimeError(f"the LLM gateway said: {(body.get('error') or {}).get('message')}")
    return body


JOB_STATES = {
    "submitted": "waiting for a Blythe GPU",
    "loading": "loading",
    "waiting": "ready",
    "answering": "answering",
    "stopped": "stopped",
    "failed": "failed",
}


def describe_gateway(status: dict) -> str:
    """One plain sentence about the model job, for a progress log."""
    if "error" in status:
        return f"model: {status['error']}"
    if not status.get("run_id"):
        return "model: not running yet (it starts with the first call)"
    which = f" {status['model']}" if status.get("model") else ""
    usage = status.get("usage") or {}
    state = JOB_STATES.get(status.get("job_state") or "", status.get("job_state") or "starting")
    working, waiting = status.get("calls_with_the_job", 0), status.get("calls_waiting_to_send", 0)
    now = f"working on {working:,} call{'s' if working != 1 else ''}" if working else "no calls now"
    if waiting:
        now += f", {waiting:,} about to be sent"
    return (
        f"model{which} on Blythe (SLURM job {status.get('slurm_job_id') or '-'}): {state}, {now};"
        f" {usage.get('calls', 0):,} calls answered in total"
    )


def served_model(model: str = MODEL) -> str:
    """The Hugging Face name of the model a call naming `model` gets (for records of which
    model made a result); `model` itself if the gateway can't be asked."""
    try:
        return model_info(model)["model"]
    except Exception:  # noqa: BLE001 - only a record: never stop the work for it
        return model
