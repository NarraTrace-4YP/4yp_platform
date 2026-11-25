"""O2 stages 3-4, shared: live progress of a wiki build, for the Windmill run and the O2 app.

Each check works out how far a build has got and reports it three ways:
- a line in the run's log (Windmill shows logs as they are written),
- the run's progress bar (wmill.set_progress), on the check step and on the whole flow run,
- the flow run's state ("progress"), and wiki.instances.stats["progress"] for the O2 app.

Fix (25 Nov 2026, problem review "O2 progress stuck at 5%"): Problem: every build sat at
"5% · files read 0/N" for hours while its wiki was visibly growing. Why: a file only counted
as read once WeKnora called it "completed", but WeKnora keeps a file "finalizing" (shown in
its pages as "generating summary") until every follow-up job for it has run: its summary,
one knowledge-graph job per chunk, and its wiki step. With the graph on that is thousands of
jobs, so nothing was ever "read". Solution: a file counts as read once WeKnora has read it
(finalizing or later), and the main measure is WeKnora's own count of follow-up jobs still to
run for the build's files (pending_subtasks_count), against the most there have been. The
line now says: files read, files finished, jobs left, the wiki queue, pages, and the model.

The percentage is rough by nature: 5% to start, 10% for reading the files, 75% for working
through the follow-up jobs, and the last 9% as files finish.
"""

from __future__ import annotations

from datetime import datetime, timezone

FINISHED = {"completed", "failed", "cancelled"}
READ = FINISHED | {"finalizing"}


def percent(
    read: int,
    done: int,
    total: int,
    pending: int | None,
    max_pending: int,
    jobs_left: int | None = None,
    max_jobs: int = 0,
) -> int:
    if not total:
        return 1
    read_part, done_part = read / total, done / total
    if jobs_left is not None and max_jobs > 0:
        worked = 1 - min(jobs_left, max_jobs) / max_jobs
    elif pending is not None and max_pending > 0:
        worked = 1 - min(pending, max_pending) / max_pending
    else:
        worked = done_part
    return max(1, min(99, round(5 + 10 * read_part + 75 * worked * read_part + 9 * done_part)))


def build_progress(
    counts: dict[str, int],
    stats: dict,
    pages: int,
    links: int,
    model_line: str | None,
    max_pending_before: int = 0,
    jobs_left: int | None = None,
    max_jobs_before: int = 0,
) -> dict:
    """counts: files by WeKnora parse_status; stats: WeKnora's wiki stats; jobs_left: the
    follow-up jobs WeKnora still has to run for the build's files (None if unknown)."""
    total = sum(counts.values())
    read = sum(n for status, n in counts.items() if status in READ)
    done = sum(n for status, n in counts.items() if status in FINISHED)
    failed = counts.get("failed", 0)
    pending = stats.get("pending_tasks")
    max_pending = max(max_pending_before, pending or 0)
    max_jobs = max(max_jobs_before, jobs_left or 0)
    active = stats.get("is_active")
    s = "s" if total != 1 else ""
    parts = [
        f"{read} of {total} review{s} read by WeKnora"
        + (f" ({failed} failed)" if failed else "")
        + f", {done} completely done"
    ]
    if jobs_left is not None and max_jobs:
        finished_jobs = max_jobs - jobs_left
        parts.append(
            f"{finished_jobs:,} of {max_jobs:,} follow-up jobs done"
            " (summaries, knowledge graph, wiki)"
        )
    if pending is None:
        parts.append("wiki: no news from WeKnora yet")
    elif active:
        parts.append(f"wiki: writing pages, {pending} review{'s' if pending != 1 else ''} to go")
    elif pending:
        parts.append(
            f"wiki: {pending} review{'s' if pending != 1 else ''} waiting to be written up"
        )
    else:
        parts.append("wiki: nothing waiting")
    parts.append(f"{pages:,} page{'s' if pages != 1 else ''} and {links:,} links so far")
    if model_line:
        parts.append(model_line)
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "line": " · ".join(parts),
        "percent": percent(read, done, total, pending, max_pending, jobs_left, max_jobs),
        "reviews_read": read,
        "reviews_done": done,
        "reviews_total": total,
        "reviews_failed": failed,
        "jobs_left": jobs_left,
        "max_jobs": max_jobs,
        "wiki_pending_tasks": pending,
        "wiki_active": active,
        "max_pending": max_pending,
        "pages": pages,
        "links": links,
        "model": model_line,
    }


def report(progress: dict, percent_override: int | None = None) -> None:
    """Prints the line and moves the run's progress bar; both are best-effort."""
    stamp = progress["at"][11:19]
    value = progress["percent"] if percent_override is None else percent_override
    print(f"[{stamp}] {value:>3}% · {progress['line']}", flush=True)
    try:
        import os

        import wmill

        if os.environ.get("WM_JOB_ID"):
            wmill.set_progress(max(0, min(99, value)))
        if os.environ.get("WM_FLOW_JOB_ID"):
            wmill.set_flow_user_state("progress", {**progress, "percent": value})
    except Exception as error:  # noqa: BLE001 (progress must never stop the build)
        print(f"(couldn't update the progress bar: {error})", flush=True)
    # The check step lasts a second; the whole run's bar is the one people watch.
    try:
        import os

        import wmill

        root = os.environ.get("WM_ROOT_FLOW_JOB_ID")
        if root and root != os.environ.get("WM_JOB_ID"):
            wmill.set_progress(max(0, min(99, value)), root)
    except Exception:  # noqa: BLE001 - Windmill may not show a flow's own bar
        pass


def report_upload(done: int, total: int, doc_id: str) -> None:
    """One line per uploaded review (all its parts), and the upload step's progress bar."""
    value = round(100 * done / total) if total else 0
    print(f"uploaded {done}/{total}: {doc_id}", flush=True)
    try:
        import os

        import wmill

        if os.environ.get("WM_JOB_ID"):
            wmill.set_progress(max(0, min(99, value)))
    except Exception:  # noqa: BLE001
        pass
