# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "wmill==1.875.0",
# ]
# ///
"""How a run started from the O1 app is going: queued, running, done or failed, its
progress bar, and the end of its log. For the O1 pipeline it also gives each step's state,
with the progress and log of the step that is running.

The app's frontend can see whether a job has finished, but not its log or progress, so it
asks this script every few seconds while something runs. It only reports on the O1
pipelines (RUNNABLE_PATHS), so it can't be used to read other jobs' logs.
"""

RUNNABLE_PATHS = {
    "f/dhr/load_dhrc",
    "f/dhr/collect_case_metadata",
    "f/dhr/o1_pipeline",
    "f/dhr/o1_status",
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
