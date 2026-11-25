# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "httpx==0.28.1",
#     "openai==3.51.0",
#     "psycopg[binary]==3.3.9",
#     "wmill==1.875.0",
# ]
# ///
"""O2: give a wiki's Chinese folder names English ones.

Fix (25 Nov 2026, problem review "O2 wiki folders in Chinese"): Problem: some wiki folders
came out in Chinese (健康与福祉 next to "Health & Wellbeing", and so on), though the pages are
English. Why: WeKnora's folder prompt has only Chinese examples, and the model sometimes copies
them (f/dhr/O2/folders.py explains). Solution: new builds start with English folders, and
check_wiki renames Chinese ones at every check. This script does the same repair by hand, for
builds made before the fix or already finished: your model gives each Chinese folder an
English name, and WeKnora renames it, or merges it into the English folder beside it that
means the same (moving its pages across). The pages' folder paths change with it. Then the
build's saved copy in the database is refreshed, so the O2 app shows the new folders.

Only for builds you made (it uses your model), and only while your model is up: start it
with f/dhr/O2/start_model first if it isn't. Safe to run again.
"""

from __future__ import annotations

from ...db import apply_migrations, connect
from ...llm import gateway_status
from ...weknora import WeKnora
from ..folders import english_folders, model_is_up
from ..wiki_store import get_instance, runner, save_wiki


def main(instance_id: str):
    wk = WeKnora()
    with connect() as conn:
        apply_migrations(conn)
        build = get_instance(conn, instance_id)
        if build["created_by"] != runner():
            raise ValueError(
                f"{instance_id} was made by {build['created_by']}: they need to run this,"
                " as it uses their model."
            )
        if not model_is_up(gateway_status()):
            raise RuntimeError(
                "Your model isn't up. Start it with f/dhr/O2/start_model, then run this again."
            )
        kb_id = build["kb_id"]
        result = english_folders(wk, kb_id)
        if result["renamed"] or result["merged"]:
            stats = conn.execute(
                "SELECT stats FROM wiki.instances WHERE instance_id = %s", (instance_id,)
            ).fetchone()[0]
            result["saved"] = save_wiki(conn, instance_id, wk.wiki_pages(kb_id), stats)
    return {"instance_id": instance_id, **result}
