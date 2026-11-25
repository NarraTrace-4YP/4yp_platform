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
    usage = status.get("usage") or {}
    state = JOB_STATES.get(status.get("job_state") or "", status.get("job_state") or "starting")
    working, waiting = status.get("calls_with_the_job", 0), status.get("calls_waiting_to_send", 0)
    now = f"working on {working:,} call{'s' if working != 1 else ''}" if working else "no calls now"
    if waiting:
        now += f", {waiting:,} about to be sent"
    return (
        f"model on Blythe (SLURM job {status.get('slurm_job_id') or '-'}): {state}, {now};"
        f" {usage.get('calls', 0):,} calls answered in total"
    )
