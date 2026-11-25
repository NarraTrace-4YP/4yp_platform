"""O2 stages 3-4, shared: live progress of a wiki build, for the Windmill run and the O2 app.

Each check works out how far a build has got and reports it three ways:
- a line in the run's log (Windmill shows logs as they are written),
- the run's progress bar (wmill.set_progress),
- the flow run's state ("progress"), and wiki.instances.stats["progress"] for the O2 app.

Counts are of files: a review uploaded in parts (f/dhr/O2/kb_settings.py) is several files.
The percentage is rough by nature: WeKnora reads each file first (the first 55%), then
writes and links pages from a queue whose length it reports (the rest, measured against the
longest that queue has been in this build).
"""

from __future__ import annotations

from datetime import datetime, timezone

FINISHED = {"completed", "failed", "cancelled"}


def percent(reviews_done: int, reviews_total: int, pending: int | None, max_pending: int) -> int:
    read = reviews_done / reviews_total if reviews_total else 0.0
    if max_pending > 0 and pending is not None:
        written = 1 - min(pending, max_pending) / max_pending
    else:
        written = 1.0 if read == 1 else 0.0
    return max(1, min(99, round(5 + 50 * read + 44 * written * read)))


def build_progress(
    counts: dict[str, int],
    stats: dict,
    pages: int,
    links: int,
    model_line: str | None,
    max_pending_before: int = 0,
) -> dict:
    total = sum(counts.values())
    done = sum(n for status, n in counts.items() if status in FINISHED)
    failed = counts.get("failed", 0)
    pending = stats.get("pending_tasks")
    max_pending = max(max_pending_before, pending or 0)
    active = stats.get("is_active")
    wiki = (
        f"wiki: {pending} task{'s' if pending != 1 else ''} queued, {'writing' if active else 'idle'}"
        if pending is not None
        else "wiki: no stats yet"
    )
    parts = [
        f"files read {done}/{total}" + (f" ({failed} failed)" if failed else ""),
        wiki,
        f"{pages:,} pages, {links:,} links",
    ]
    if model_line:
        parts.append(model_line)
    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "line": " · ".join(parts),
        "percent": percent(done, total, pending, max_pending),
        "reviews_done": done,
        "reviews_total": total,
        "reviews_failed": failed,
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


def report_upload(done: int, total: int, doc_id: str) -> None:
    """One line per uploaded review, and the progress bar of the upload step."""
    value = round(100 * done / total) if total else 0
    print(f"uploaded {done}/{total}: {doc_id}", flush=True)
    try:
        import os

        import wmill

        if os.environ.get("WM_JOB_ID"):
            wmill.set_progress(max(0, min(99, value)))
    except Exception:  # noqa: BLE001
        pass
