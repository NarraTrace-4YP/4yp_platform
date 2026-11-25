# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""Tool: watch your LLM work live (the gateway's model job, and an O2 wiki build if you give one).

For the length of the run, writes one line to the log every `every_seconds`: your model
job's state on Blythe as the LLM gateway sees it (SLURM job, calls answering and queued),
how many calls and tokens it has done, and the speed since the last line. With an O2
instance_id it adds that build's progress from WeKnora (reviews read, wiki queue, pages) and
moves the progress bar with it. Open the run in Windmill to watch the lines arrive.

It only reads: the gateway's own status (it never contacts Blythe itself), WeKnora with
your own key, and the database. Stopping it (Cancel in Windmill) stops nothing else. It
holds one Windmill worker while it watches, so keep `minutes` sensible.
"""

from __future__ import annotations

import os
import time

from ..llm import describe_gateway, gateway_status


def o2_progress(instance_id: str, max_pending: int) -> dict:
    from ..db import connect
    from ..O2.progress import build_progress
    from ..O2.wiki_store import get_instance
    from ..weknora import WeKnora

    wk = WeKnora()
    with connect() as conn:
        build = get_instance(conn, instance_id)
        expected = [
            r[0]
            for r in conn.execute(
                "SELECT knowledge_id FROM wiki.documents WHERE instance_id = %s", (instance_id,)
            ).fetchall()
        ]
    by_id = {d.get("id"): d for d in wk.documents(build["kb_id"])}
    counts: dict[str, int] = {}
    for knowledge_id in expected:
        status = (by_id.get(knowledge_id) or {}).get("parse_status") or "not uploaded"
        counts[status] = counts.get(status, 0) + 1
    stats = wk.wiki_stats(build["kb_id"]) or {}
    pages = stats.get("total_pages") or 0
    links = stats.get("total_links") or 0
    return build_progress(counts, stats, pages, links, None, max_pending)


def main(minutes: int = 15, every_seconds: int = 30, instance_id: str = ""):
    minutes = max(1, min(minutes, 240))
    every_seconds = max(10, min(every_seconds, 600))
    end = time.monotonic() + minutes * 60
    last_tokens = last_calls = None
    last_time = time.monotonic()
    max_pending = 0
    lines = 0
    latest: dict = {}
    while True:
        now = time.monotonic()
        status = gateway_status()
        usage = status.get("usage") or {}
        line = describe_gateway(status)
        tokens, calls = usage.get("completion_tokens"), usage.get("calls")
        if last_tokens is not None and tokens is not None:
            minutes_passed = max((now - last_time) / 60, 1e-6)
            line += (
                f" · last {round(now - last_time)} s: {calls - last_calls} calls,"
                f" {round((tokens - last_tokens) / minutes_passed):,} tokens/min"
            )
        last_tokens, last_calls, last_time = tokens, calls, now
        value = None
        if instance_id:
            try:
                o2 = o2_progress(instance_id, max_pending)
                max_pending = o2["max_pending"]
                line = f"{o2['line']} · {line}"
                value = o2["percent"]
            except Exception as error:  # noqa: BLE001 (keep watching the model regardless)
                line = f"O2 build: {error} · {line}"
        if value is None:
            value = round(100 * (1 - max(end - now, 0) / (minutes * 60)))
        print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)
        lines += 1
        latest = {"line": line, "gateway": status}
        if os.environ.get("WM_JOB_ID"):
            try:
                import wmill

                wmill.set_progress(max(0, min(99, value)))
            except Exception:  # noqa: BLE001
                pass
        if now + every_seconds > end:
            break
        time.sleep(every_seconds)
    return {"lines": lines, **latest}
