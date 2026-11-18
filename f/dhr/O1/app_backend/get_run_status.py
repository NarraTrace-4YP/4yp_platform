# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "wmill==1.875.0",
# ]
# ///
"""Return progress and recent logs for runs started by the O1 app.

Only paths in ``RUNNABLE_PATHS`` may be inspected. For a flow, report each step's
state and show logs from the running step, or the last completed/failed step.
Missing progress information must not prevent the app from displaying a run.
"""

RUNNABLE_PATHS = {
    "f/dhr/O1/load_reviews/load_reviews",
    "f/dhr/O1/link_case_metadata/link_case_metadata",
    "f/dhr/O1/group_blocks_by_section/group_blocks_by_section",
    "f/dhr/O1/find_candidate_template_sections/find_candidate_template_sections",
    "f/dhr/O1/pipeline",
    "f/dhr/O1/app_backend/get_status",
}
LOG_LINES = 40

# Windmill's names for a flow step's state, in plain words.
STEP_STATES = {
    "WaitingForPriorSteps": "waiting",
    "WaitingForEvents": "waiting",
    "WaitingForExecutor": "starting",
    "InProgress": "running",
    "Success": "done",
    "Failure": "failed",
}


def job_state(job: dict) -> str:
    """queued, running, done, failed or cancelled."""
    if job.get("type") == "CompletedJob":
        if job.get("canceled"):
            return "cancelled"
        return "done" if job.get("success") else "failed"
    return "running" if job.get("running") else "queued"


def log_tail(logs: str | None, lines: int = LOG_LINES) -> list[str]:
    """The last lines of a log, without blank ones."""
    return [line for line in (logs or "").splitlines() if line.strip()][-lines:]


def error_message(result) -> str | None:
    """The message of a failed job's error, which Windmill stores as its result."""
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        error = result["error"]
        return error.get("message") or error.get("name") or str(error)
    return None


def flow_steps(job: dict) -> list[dict]:
    """Each step of a flow: its id, plain-words state and the job running it (if any)."""
    steps = []
    for module in (job.get("flow_status") or {}).get("modules") or []:
        state = STEP_STATES.get(module.get("type"), "waiting")
        if module.get("skipped"):
            state = "skipped"
        steps.append({"id": module.get("id"), "state": state, "job_id": module.get("job")})
    return steps


def current_step(steps: list[dict]) -> dict | None:
    """The step to show the log of: the one running, else the last one that ran."""
    for step in steps:
        if step["state"] == "running" and step["job_id"]:
            return step
    ran = [s for s in steps if s["job_id"] and s["state"] in ("done", "failed")]
    return ran[-1] if ran else None


def summarise(job: dict, progress=None, step_job: dict | None = None) -> dict:
    """What the app shows for a run. step_job is the running (or last) step of a flow."""
    state = job_state(job)
    shown = step_job or job
    summary = {
        "job_id": job.get("id"),
        "path": job.get("script_path"),
        "state": state,
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "duration_ms": job.get("duration_ms"),
        "progress": progress,
        "log": log_tail(shown.get("logs")),
        "result": job.get("result") if state == "done" else None,
        "error": error_message(job.get("result")) if state == "failed" else None,
        "steps": None,
    }
    if state == "failed" and summary["error"] is None and step_job is not None:
        summary["error"] = error_message(step_job.get("result"))
    return summary


def progress_of(job_id: str):
    """The job's progress bar (0-100), or None if it has none."""
    import wmill

    try:
        return wmill.get_progress(job_id)
    except Exception:  # noqa: BLE001 - progress is a nice-to-have; never fail the check on it
        return None


def main(job_id: str = ""):
    """job_id: a run the O1 app started."""
    import wmill  # Windmill's client: only there when running in Windmill

    if not job_id:
        raise ValueError("Give the id of a run")
    job = wmill.get_job(job_id)
    if job.get("script_path") not in RUNNABLE_PATHS:
        raise ValueError(f"{job_id} isn't an O1 pipeline run")
    steps = flow_steps(job) if job.get("job_kind") == "flow" or job.get("flow_status") else None
    if not steps:
        return summarise(job, progress_of(job_id) if job_state(job) == "running" else None)
    step = current_step(steps)
    step_job = wmill.get_job(step["job_id"]) if step else None
    progress = progress_of(step["job_id"]) if step and step["state"] == "running" else None
    summary = summarise(job, progress, step_job)
    summary["steps"] = steps
    summary["current_step"] = step["id"] if step else None
    return summary
