"""Gives every f/dhr script in Windmill the first line of its docstring as its summary.

Windmill lists scripts by their summary, but `wmill generate-metadata` writes each script's
metadata file (*.script.yaml, not kept in Git) with an empty one, and `wmill sync push` then
sends that empty summary, wiping whatever was set in Windmill. The deploy runs this between
the two (.github/workflows/deploy.yml), so the summary lives in the code, where the checks
can see it: in f/dhr/O1 every docstring starts "O1 stage N: ..." (tests/test_script_summaries.py).

    python3 .github/script_summaries.py          # fill in the generated metadata files
    python3 .github/script_summaries.py --check  # only list what it would set

Standard library only, like windmill_sync.py.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "f" / "dhr"
SUMMARY_LINE = re.compile(r"^summary:.*$", re.MULTILINE)


def summary_of(path: Path) -> str | None:
    """A script's docstring's first line, without its closing full stop (None: no docstring)."""
    docstring = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")), clean=True)
    if not docstring or not docstring.strip():
        return None
    return docstring.strip().splitlines()[0].strip().rstrip(".")


def set_summary(metadata: str, summary: str) -> str:
    """The metadata file's text with `summary` as its summary. A JSON string is a valid YAML
    double-quoted string, so quotes and dashes in the summary are written safely."""
    line = f"summary: {json.dumps(summary, ensure_ascii=False)}"
    if SUMMARY_LINE.search(metadata):
        return SUMMARY_LINE.sub(lambda _: line, metadata, count=1)
    return f"{line}\n{metadata}"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    changed = 0
    for script in sorted(SCRIPTS.rglob("*.py")):
        if "__pycache__" in script.parts:
            continue
        summary = summary_of(script)
        if summary is None:
            continue
        metadata = script.with_suffix(".script.yaml")
        rel = script.relative_to(ROOT)
        if check or not metadata.exists():
            print(f"{rel}: {summary}" + ("" if check else " (no metadata file, skipped)"))
            continue
        text = metadata.read_text(encoding="utf-8")
        new = set_summary(text, summary)
        if new != text:
            metadata.write_text(new, encoding="utf-8")
            changed += 1
    if not check:
        print(f"Set {changed} script summaries from their docstrings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
