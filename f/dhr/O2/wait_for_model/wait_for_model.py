# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "openai==3.51.0",
# ]
# ///
"""O2 stage 2: is your model ready yet? One check, run every few minutes by the flow.

Asks the LLM gateway how your model job is doing on Blythe. While SLURM keeps it queued (busy
GPUs: hours, sometimes days) or the model is loading, it returns ready: false and the flow
sleeps until the next check without holding a worker. Once the job is answering, it sends one
tiny question to make sure, and returns ready: true.

If the job has ended without answering (time limit, cancelled), a new one is submitted. If it
failed (e.g. the GPUs couldn't talk to each other, or the model isn't downloaded), this stops
the flow with the reason instead of trying again for days.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ...llm import client, gateway_refresh, gateway_start

READY = ("waiting", "answering")
EXPLAINED = {
    None: "waiting for Blythe GPUs",
    "submitted": "waiting for Blythe GPUs",
    "loading": "loading the model",
}


def main(model: str = "default"):
    """model: the gateway's key for the build's model (check_setup's "model")."""
    status = gateway_refresh(model)
    if status.get("last_failure") and not status.get("run_id"):
        raise RuntimeError(
            f"Your model job on Blythe failed: {status['last_failure']}. Fix that (see the job's"
            " slurm-<id>.out in ~/narratrace/runs/<run_id>/), then run the flow again."
        )
    if not status.get("run_id"):
        status = gateway_start(model)  # it ended (time limit, idle, cancelled): queue a new one
    state, slurm = status.get("job_state"), status.get("slurm") or {}
    checked = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    found = {
        "checked": checked,
        "model": status.get("model") or model,
        "run_id": status.get("run_id"),
        "slurm_job_id": status.get("slurm_job_id"),
        "job_state": state,
        "slurm_state": slurm.get("state"),
        "slurm_reason": slurm.get("reason"),
    }
    if state not in READY:
        where = EXPLAINED.get(state, state)
        if slurm.get("reason") and state in (None, "submitted"):
            where += f" (SLURM: {slurm.get('state')}, {slurm['reason']})"
        print(f"{checked}: not ready yet: {where}")
        return {**found, "ready": False}
    reply = client(timeout=600).chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        max_tokens=5,
    )
    print(f"{checked}: ready, {reply.model} answered {reply.choices[0].message.content!r}")
    return {**found, "ready": True}
