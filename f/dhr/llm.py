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

MODEL = "Qwen/Qwen3.8-27B-FP8"
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
    return OpenAI(base_url=url, api_key=token, timeout=TIMEOUT_SECONDS, **options)


def chat_model(**options):
    """A LangChain ChatOpenAI for the gateway (tools, structured output and LangGraph work)."""
    from langchain_openai import ChatOpenAI

    url, token = connection()
    return ChatOpenAI(model=MODEL, base_url=url, api_key=token, timeout=TIMEOUT_SECONDS, **options)


def gateway_status() -> dict:
    """What the gateway knows of your model right now, without contacting Blythe: your job's
    state (SLURM job id, run folder), calls queued and with the job, and your calls and tokens
    so far. {"error": ...} if the gateway can't be asked."""
    import httpx

    url, token = connection()
    base = url.removesuffix("/v1").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        status = httpx.get(f"{base}/status", headers=headers, timeout=10).json()
        usage = httpx.get(f"{base}/usage", headers=headers, timeout=10).json()
    except (httpx.HTTPError, ValueError) as error:
        return {"error": f"can't ask the gateway: {error}"}
    return {**status, "usage": usage}


def describe_gateway(status: dict) -> str:
    """One line about the model job, for a progress log."""
    if "error" in status:
        return f"model: {status['error']}"
    if not status.get("run_id"):
        return "model: no job running yet"
    usage = status.get("usage") or {}
    return (
        f"model: {status.get('job_state') or '?'} (SLURM {status.get('slurm_job_id') or '-'}),"
        f" {status.get('calls_with_the_job', 0)} answering, {status.get('calls_waiting_to_send', 0)} queued,"
        f" {usage.get('calls', 0):,} calls / {usage.get('completion_tokens', 0):,} tokens written so far"
    )
