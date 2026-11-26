# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
# ]
# ///
"""O2 stage 2: submit the model job on your own Blythe account (returns at once).

Asks the LLM gateway, as the person running the flow, to submit their model job if it isn't
running. It doesn't wait for it: SLURM may keep the job queued for hours or days when the GPUs
are busy, so the flow's next step, wait_for_model, checks back every few minutes without
holding a worker (26 Nov 2026: waiting inside this step timed out after 70 minutes).
"""

from __future__ import annotations

from ...llm import gateway_start


def main(model: str = "default"):
    """model: the gateway's key for the build's model (check_setup's "model")."""
    status = gateway_start(model)
    slurm = status.get("slurm") or {}
    return {
        "model": status.get("model"),
        "run_id": status.get("run_id"),
        "slurm_job_id": status.get("slurm_job_id"),
        "job_state": status.get("job_state"),
        "slurm_state": slurm.get("state"),
        "slurm_reason": slurm.get("reason"),
    }
