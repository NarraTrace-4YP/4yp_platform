"""O2 stages 3-4, shared: keep the wiki's folder names in English.

Fix (25 Nov 2026, problem review "O2 wiki folders in Chinese"):
Problem: some of a wiki's folders came out in Chinese (健康与福祉, 案例与人物, 法律概念 ...),
next to English ones for the same topics ("Health & Wellbeing"), although every page is in
English. Why: WeKnora (v0.9.0) files each batch's new pages into folders with one model call,
using a prompt that is built into its program (WikiTaxonomyPlanPrompt in
internal/agent/prompts_wiki.go). The prompt does say "write all folder labels in English"
(WEKNORA_LANGUAGE in compose.yaml), but every example in it is Chinese ("组织", "法律概念",
"人物", "节日"), and the model sometimes copies the examples rather than the instruction. Once
one Chinese folder exists the prompt tells the model to reuse existing folders exactly, so
later batches keep filing into it. Pages, summaries, the knowledge graph and everything
else use other prompts and are not affected.
Solution, without changing WeKnora's program (we run its published image):
1. Before uploading, create_wiki makes a set of English top-level folders for DHR wikis
   (SEED_FOLDERS). The folder prompt shows the model the folders that already exist and
   tells it to reuse them, so from the very first batch it is shown English labels to copy.
2. At every check, check_wiki looks for folder names with Chinese in them and, if there are
   any and the build's own model is already up, asks the model for English names
   (english_folders) and renames them in WeKnora. A name that means the same as an English
   folder beside it is merged into that folder (its pages and sub-folders are moved across
   and the empty Chinese folder deleted). WeKnora updates the pages' paths itself.
   f/dhr/O2/english_folders runs the same repair on its own, for builds made before this.
"""

from __future__ import annotations

import json
import re

# Broad shelves for a DHR wiki. The model may still add others (in English, copying these).
SEED_FOLDERS = [
    "People",
    "Organisations & Agencies",
    "Places",
    "Events & Incidents",
    "Domestic Abuse",
    "Health & Wellbeing",
    "Safeguarding & Risk",
    "Criminal Justice & Law",
    "Professional Practice",
    "Reviews & Recommendations",
]

_CHINESE = re.compile("[㐀-鿿豈-﫿]")


def has_chinese(text: str | None) -> bool:
    return bool(text and _CHINESE.search(text))


def seed_folders(wk, kb_id: str, names: list[str] = SEED_FOLDERS) -> list[str]:
    """Creates the English top-level folders that aren't there yet; returns the names made."""
    have = {f.get("name") for f in wk.wiki_folders(kb_id)}
    made = []
    for name in names:
        if name in have:
            continue
        try:
            wk.create_wiki_folder(kb_id, name)
            made.append(name)
        except Exception as error:  # a folder made meanwhile (409): fine
            if "409" not in str(error):
                raise
    return made


def all_folders(wk, kb_id: str) -> list[dict]:
    """Every folder, parents before their children."""
    found, todo = [], [""]
    while todo:
        parent = todo.pop(0)
        for f in wk.wiki_folders(kb_id, parent):
            found.append(f)
            if f.get("has_children"):
                todo.append(f["id"])
    return found


def model_is_up(status: dict) -> bool:
    """True if the build's own model job is loaded and answering (so a call won't start a
    job or wait for a GPU)."""
    return status.get("job_state") in ("waiting", "answering")


PROMPT = """These are folder names in a wiki about Domestic Homicide Reviews (England and \
Wales). Some came out in Chinese by mistake. Give each Chinese name a short English folder \
name (British English, title case, at most 4 words) that means the same.

If one of the English folders already beside it (same parent) means the same thing, give \
that English name exactly, so the two are merged.

Chinese folders (parent path → name), with the English folders already beside each:
{items}

Answer with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "english": {"type": "string"}},
                "required": ["id", "english"],
            },
        }
    },
    "required": ["translations"],
}


def ask_model(prompt: str) -> str:
    """One short call to the runner's model through the gateway (waits at most 3 minutes)."""
    from ..llm import MODEL, client

    reply = client(timeout=180, max_retries=0).chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1500,
        temperature=0,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "folders", "schema": SCHEMA},
        },
    )
    return reply.choices[0].message.content or ""


def translate(chinese: list[dict], folders: list[dict], ask=ask_model) -> dict[str, str]:
    """{folder id: English name} for the Chinese folders, from one model call."""
    by_parent: dict[str, list[str]] = {}
    for f in folders:
        if not has_chinese(f.get("name")):
            by_parent.setdefault(f.get("parent_id") or "", []).append(f["name"])
    lines = []
    for f in chinese:
        parent = f["path"].rsplit("/", 1)[0] if "/" in f["path"] else "(top level)"
        beside = by_parent.get(f.get("parent_id") or "", [])
        lines.append(
            f"- id {f['id']}: {parent} → {f['name']}"
            f" | English beside it: {', '.join(beside) if beside else 'none'}"
        )
    raw = ask(PROMPT.format(items="\n".join(lines)))
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    wanted = {f["id"] for f in chinese}
    out = {}
    for t in json.loads(raw).get("translations") or []:
        name = " ".join(str(t.get("english") or "").replace("/", " & ").split())[:60]
        if t.get("id") in wanted and name and not has_chinese(name):
            out[t["id"]] = name
    return out


def merge_into(wk, kb_id: str, source: dict, target: dict, pages: list[dict]) -> int:
    """Moves source's pages and sub-folders into target (merging sub-folders with the same
    name), then deletes source. Returns the pages moved."""
    moved = 0
    for page in pages:
        if page.get("folder_id") == source["id"]:
            wk.move_wiki_page(kb_id, page["slug"], target["id"])
            moved += 1
    inside = {f["name"]: f for f in wk.wiki_folders(kb_id, target["id"])}
    for child in wk.wiki_folders(kb_id, source["id"]):
        if child["name"] in inside:
            moved += merge_into(wk, kb_id, child, inside[child["name"]], pages)
        else:
            wk.update_wiki_folder(kb_id, child["id"], parent_id=target["id"])
    wk.delete_wiki_folder(kb_id, source["id"])
    return moved


def english_folders(wk, kb_id: str, ask=ask_model, rounds: int = 4) -> dict:
    """Renames (or merges) every folder whose name has Chinese in it, one level at a time
    from the top (one model call per level), reading the folders afresh each time, since a
    merge moves sub-folders. Safe to repeat."""
    found = renamed = merged = moved = 0
    pages = None
    for _ in range(rounds):
        folders = all_folders(wk, kb_id)
        chinese = [f for f in folders if has_chinese(f.get("name"))]
        if not chinese:
            break
        found = max(found, len(chinese))
        top = min(f.get("depth") or 0 for f in chinese)
        level = [f for f in chinese if (f.get("depth") or 0) == top]
        names = translate(level, folders, ask)
        if not names:
            break
        for f in level:
            english = names.get(f["id"])
            if not english:
                continue
            siblings = {s["name"]: s for s in folders if s.get("parent_id") == f.get("parent_id")}
            target = siblings.get(english)
            if target and target["id"] != f["id"]:
                if pages is None:
                    pages = wk.wiki_pages(kb_id)
                moved += merge_into(wk, kb_id, f, target, pages)
                merged += 1
            else:
                wk.update_wiki_folder(kb_id, f["id"], name=english)
                renamed += 1
                folders.append({**f, "name": english})  # a later same-name one merges into it
    left = sum(1 for f in all_folders(wk, kb_id) if has_chinese(f.get("name"))) if found else 0
    return {
        "chinese": found,
        "renamed": renamed,
        "merged": merged,
        "pages_moved": moved,
        "left": left,
    }
