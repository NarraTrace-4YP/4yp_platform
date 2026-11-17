"""Tests for .github/windmill_sync.py, which brings edits made in Windmill back to GitHub and
stops deploys from overwriting them. (The parts that talk to Windmill, Git and GitHub were
rehearsed against stand-ins; these test the decisions.)"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windmill_sync", ROOT / ".github/windmill_sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

LAST_DEPLOY = {"f/dhr/a.py": b"a = 1\n", "f/dhr/b.py": b"b = 1\n"}


def write(root: Path, tree: dict[str, bytes]) -> Path:
    for name, contents in tree.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(contents)
    return root


def test_only_files_git_keeps_are_compared(tmp_path):
    write(
        tmp_path,
        {
            "f/dhr/a.py": b"a",
            "f/dhr/a.script.yaml": b"made by wmill",
            "f/dhr/a.script.lock": b"made by wmill",
            "f/dhr/x.flow/step.inline_script.lock": b"made by wmill",
            "f/dhr/o1.raw_app/App.tsx": b"app",
            "f/dhr/o1.raw_app/node_modules/react/index.js": b"package",
            "f/dhr/o1.raw_app/dist/bundle.js": b"built",
            "f/dhr/o1.raw_app/package-lock.json": b"made by npm",
            "f/dhr/version.py": b'GIT_COMMIT = "abc"',  # written by each deploy
            "README.md": b"not under f/",
        },
    )
    assert sorted(sync.files(tmp_path)) == ["f/dhr/a.py", "f/dhr/o1.raw_app/App.tsx"]


def test_nothing_edited_in_windmill_lets_the_deploy_go_ahead():
    deploying = {**LAST_DEPLOY, "f/dhr/b.py": b"b = 2\n"}  # a change from GitHub
    assert sync.unsynced_edits(LAST_DEPLOY, LAST_DEPLOY, deploying) == []


def test_a_windmill_edit_the_commit_lacks_stops_the_deploy():
    windmill = {**LAST_DEPLOY, "f/dhr/a.py": b"a = 2\n"}
    deploying = {**LAST_DEPLOY, "f/dhr/b.py": b"b = 2\n"}
    assert sync.unsynced_edits(windmill, LAST_DEPLOY, deploying) == ["f/dhr/a.py"]


def test_deploying_the_windmill_edits_themselves_goes_ahead():
    windmill = {**LAST_DEPLOY, "f/dhr/a.py": b"a = 2\n"}
    assert sync.unsynced_edits(windmill, LAST_DEPLOY, dict(windmill)) == []


def test_scripts_added_or_deleted_in_windmill_stop_the_deploy():
    added = {**LAST_DEPLOY, "f/dhr/new.py": b"new"}
    assert sync.unsynced_edits(added, LAST_DEPLOY, LAST_DEPLOY) == ["f/dhr/new.py"]
    deleted = {"f/dhr/a.py": LAST_DEPLOY["f/dhr/a.py"]}
    assert sync.unsynced_edits(deleted, LAST_DEPLOY, LAST_DEPLOY) == ["f/dhr/b.py"]


def test_windmill_edits_are_copied_into_the_repo(tmp_path):
    repo = write(tmp_path, {**LAST_DEPLOY, "f/dhr/c.py": b"untouched"})
    windmill = {"f/dhr/a.py": b"a = 2\n", "f/dhr/new.py": b"new"}  # b.py deleted
    edits = sync.apply_edits(windmill, LAST_DEPLOY, repo)
    assert edits == [
        ("f/dhr/a.py", "changed"),
        ("f/dhr/b.py", "deleted"),
        ("f/dhr/new.py", "added"),
    ]
    assert sync.files(repo) == {
        "f/dhr/a.py": b"a = 2\n",
        "f/dhr/c.py": b"untouched",
        "f/dhr/new.py": b"new",
    }


def test_files_map_to_windmill_items():
    assert sync.windmill_item("f/dhr/load_dhrc.py") == ("script", "f/dhr/load_dhrc")
    assert sync.windmill_item("f/dhr/o1_pipeline.flow/flow.yaml") == ("flow", "f/dhr/o1_pipeline")
    assert sync.windmill_item("f/dhr/o1.raw_app/backend/status.yaml") == ("app", "f/dhr/o1")
    assert sync.windmill_item("f/dhr/notes.txt") is None


def test_pull_request_warns_when_github_changed_the_same_file():
    edits = [("f/dhr/a.py", "changed"), ("f/dhr/new.py", "added")]
    body = sync.pr_body(edits, {"f/dhr/a.py": "ks0610"}, ["f/dhr/a.py"])
    assert "| `f/dhr/a.py` | changed | ks0610 |" in body
    assert "| `f/dhr/new.py` | added |  |" in body
    assert "Also changed on GitHub since the last deploy:** `f/dhr/a.py`" in body
    assert "Also changed on GitHub" not in sync.pr_body(edits, {}, [])


def test_gitignore_keeps_what_the_sync_ignores_out_of_git():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").split()
    for pattern in ("*.script.yaml", "*.script.lock", "node_modules/", "dist/"):
        assert pattern in ignored
    assert "*.raw_app/package-lock.json" in ignored
