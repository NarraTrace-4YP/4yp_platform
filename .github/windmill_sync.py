"""Carries edits made in Windmill's web editor back to GitHub, and stops a deploy from
overwriting edits that haven't reached GitHub yet.

The team code (f/dhr) can be changed in two places: on GitHub (a pull request, which
deploys to Windmill when merged) or in Windmill's web editor (live straight away). GitHub
stays the record of what runs because:

- after every deploy, `snapshot` saves a copy of what Windmill holds (in Windmill's own
  file format) on the branch "windmill-snapshot";
- every 15 minutes, `sync` fetches what Windmill holds now. Anything that differs from
  the snapshot was edited in Windmill, so it goes onto the branch "windmill-changes" as a
  pull request ("Changes made in Windmill"), with the checks run on it;
- before every deploy, `guard` stops it if Windmill has edits that the commit being
  deployed doesn't include, because the deploy would overwrite them.

    python .github/windmill_sync.py sync       (.github/workflows/windmill-to-github.yml)
    python .github/windmill_sync.py guard      (.github/workflows/deploy.yml)
    python .github/windmill_sync.py snapshot   (.github/workflows/deploy.yml)

Run from the repo root, with WINDMILL_URL and WINDMILL_TOKEN set (as for the deploy), and
the wmill and git commands. `sync` also needs the gh command with GH_TOKEN.
"""

from __future__ import annotations

import fnmatch
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

WORKSPACE = "dhr"
SNAPSHOT_BRANCH = "windmill-snapshot"
CHANGES_BRANCH = "windmill-changes"
PR_TITLE = "Changes made in Windmill"
DEPLOYED_COMMIT = "DEPLOYED_COMMIT"  # file on the snapshot branch: the commit deployed
BOT = ["-c", "user.name=github-actions[bot]"]
BOT += ["-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]

# Files Windmill's tool writes that aren't kept in Git (they're in .gitignore), and the
# file the deploy itself writes. These are never compared or copied.
IGNORED = (
    "*.script.yaml",
    "*.script.lock",
    "*.lock",
    "*/wmill-lock.yaml",
    "*/node_modules/*",
    "*/dist/*",
    "*.raw_app/package-lock.json",
)
NOT_SYNCED = {"f/dhr/version.py"}


def say(message: str) -> None:
    print(message, flush=True)


class SyncError(RuntimeError):
    """Something needs a person: the message says what."""


# ----------------------------------------------------------------- comparing file trees


def files(root: Path) -> dict[str, bytes]:
    """Every file under root/f that Git would keep, as {"f/dhr/x.py": contents}."""
    found = {}
    for path in sorted((root / "f").rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if name in NOT_SYNCED or any(fnmatch.fnmatch(name, p) for p in IGNORED):
            continue
        found[name] = path.read_bytes()
    return found


def changed(a: dict[str, bytes], b: dict[str, bytes]) -> list[str]:
    """Files that differ between two trees, including files only one has."""
    return sorted(name for name in a.keys() | b.keys() if a.get(name) != b.get(name))


def unsynced_edits(
    windmill: dict[str, bytes], snapshot: dict[str, bytes], deploying: dict[str, bytes]
) -> list[str]:
    """Files edited in Windmill since the last deploy that the commit being deployed
    doesn't have exactly as Windmill has them: deploying would overwrite them."""
    return [n for n in changed(windmill, snapshot) if windmill.get(n) != deploying.get(n)]


def apply_edits(
    windmill: dict[str, bytes], snapshot: dict[str, bytes], repo: Path
) -> list[tuple[str, str]]:
    """Copies the files edited in Windmill since the last deploy into the repo (deleting
    those Windmill deleted). Returns [(file, "added" / "changed" / "deleted")]."""
    edits = []
    for name in changed(windmill, snapshot):
        target = repo / name
        if name in windmill:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(windmill[name])
            edits.append((name, "changed" if name in snapshot else "added"))
        else:
            if target.exists():
                target.unlink()
            edits.append((name, "deleted"))
    return edits


def windmill_item(name: str) -> tuple[str, str] | None:
    """Which Windmill item a file belongs to: ("script" | "flow" | "app", path)."""
    match = re.match(r"(f/.+?)\.(flow|raw_app)/", name)
    if match:
        return ("flow" if match.group(2) == "flow" else "app", match.group(1))
    match = re.fullmatch(r"(f/.+)\.(py|ts|sh|sql|go|ps1|php|rs)", name)
    return ("script", match.group(1)) if match else None


def pr_body(
    edits: list[tuple[str, str]], editors: dict[str, str], also_on_github: list[str]
) -> str:
    rows = "\n".join(f"| `{name}` | {kind} | {editors.get(name, '')} |" for name, kind in edits)
    body = [
        (
            "Someone edited the team code in Windmill's web editor. This brings those edits "
            "to GitHub, so GitHub stays the record of what runs. Windmill is already "
            "running them."
        ),
        "",
        "| File | Change | Last saved in Windmill by |",
        "| --- | --- | --- |",
        rows,
    ]
    if also_on_github:
        listed = ", ".join(f"`{name}`" for name in also_on_github)
        body += [
            "",
            (
                f"**Also changed on GitHub since the last deploy:** {listed}. This shows the "
                "Windmill version of these files, so check the diff: if a change from GitHub "
                "has been undone, put it back in a separate pull request after merging this."
            ),
        ]
    body += [
        "",
        (
            "Review it as usual. Merging it deploys main to Windmill, which leaves these "
            "edits as they are. Until it's merged, other deploys stop rather than overwrite "
            "them."
        ),
        "",
        (
            "Don't push to this branch: it's rebuilt automatically every 15 minutes when "
            "there are newer edits in Windmill. To change something, edit it in Windmill "
            "(it shows up here) or open a separate pull request after merging this one."
        ),
        "",
        "_Made by `.github/windmill_sync.py` (Actions > windmill to github)._",
    ]
    return "\n".join(body) + "\n"


# ----------------------------------------------------------------- talking to the tools


def run(command: list[str], cwd: Path | None = None, check: bool = True, **kwargs):
    result = subprocess.run(command, check=False, cwd=cwd, capture_output=True, text=True, **kwargs)
    if check and result.returncode != 0:
        # Never echo the command: it may contain the Windmill token.
        raise SyncError(f"{command[0]} {command[1]} failed:\n{result.stderr or result.stdout}")
    return result


def windmill_settings() -> tuple[str, str]:
    url, token = os.environ.get("WINDMILL_URL", ""), os.environ.get("WINDMILL_TOKEN", "")
    if not url or not token:
        raise SyncError("Set WINDMILL_URL and WINDMILL_TOKEN (see deploy.yml)")
    return url.rstrip("/"), token


def pull_windmill(repo: Path, dest: Path) -> Path:
    """Fetches what Windmill holds in f/dhr into dest, the way the deploy pushes it."""
    url, token = windmill_settings()
    dest.mkdir(parents=True)
    shutil.copy(repo / "wmill.yaml", dest / "wmill.yaml")
    run(
        ["wmill", "sync", "pull", "--yes", "--workspace", WORKSPACE]
        + ["--base-url", url, "--token", token],
        cwd=dest,
    )
    return dest


def read_snapshot(repo: Path, dest: Path) -> tuple[dict[str, bytes], str] | None:
    """The snapshot branch's files and the commit it was taken after, or None if there
    isn't one yet."""
    ref = f"refs/remotes/origin/{SNAPSHOT_BRANCH}"
    fetched = run(
        ["git", "fetch", "--depth=1", "origin", f"+refs/heads/{SNAPSHOT_BRANCH}:{ref}"],
        cwd=repo,
        check=False,
    )
    if fetched.returncode != 0:
        if "couldn't find remote ref" in fetched.stderr:
            return None
        raise SyncError(f"couldn't fetch {SNAPSHOT_BRANCH}:\n{fetched.stderr}")
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref], cwd=repo, capture_output=True, check=True
    )
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return files(dest), (dest / DEPLOYED_COMMIT).read_text(encoding="utf-8").strip()


def write_snapshot(repo: Path, windmill: Path, deployed_commit: str, message: str) -> None:
    """Replaces the snapshot branch with what Windmill holds (only the files Git keeps)."""
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp) / "stage"
        for name, contents in files(windmill).items():
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            (stage / name).write_bytes(contents)
        stage.mkdir(exist_ok=True)
        (stage / DEPLOYED_COMMIT).write_text(deployed_commit + "\n", encoding="utf-8")
        (stage / "README.md").write_text(
            "What Windmill held in f/dhr just after the last deploy (in Windmill's own "
            "format), written by .github/windmill_sync.py. Edits made in Windmill are found "
            "by comparing with this. Don't change this branch by hand.\n",
            encoding="utf-8",
        )
        git = ["git", f"--git-dir={repo / '.git'}", f"--work-tree={stage}"]
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        run([*git, "add", "--all", "--force", "."], cwd=stage, env=env)
        tree = run([*git, "write-tree"], cwd=stage, env=env).stdout.strip()
        commit = run(
            ["git", *BOT, "commit-tree", tree, "-m", message], cwd=repo, env=env
        ).stdout.strip()
    run(["git", "push", "--force", "origin", f"{commit}:refs/heads/{SNAPSHOT_BRANCH}"], cwd=repo)


def who_saved(edits: list[tuple[str, str]]) -> dict[str, str]:
    """Who last saved each edited item in Windmill. Best effort: blank if unknown."""
    url, token = windmill_settings()
    endpoints = {"script": "scripts/get/p", "flow": "flows/get", "app": "apps/get/p"}
    editors, cache = {}, {}
    for name, kind in edits:
        item = windmill_item(name)
        if item is None or kind == "deleted":
            continue
        if item not in cache:
            cache[item] = ""
            api = f"{url}/api/w/{WORKSPACE}/{endpoints[item[0]]}/{urllib.parse.quote(item[1])}"
            request = urllib.request.Request(api, headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    details = json.load(response)
                cache[item] = details.get("edited_by") or details.get("created_by") or ""
            except Exception as error:  # noqa: BLE001 - only a nicety for the pull request
                say(f"(couldn't look up who saved {item[1]}: {error})")
        editors[name] = cache[item]
    return editors


def gh(*args: str, check: bool = True):
    result = run(["gh", *args], check=False)
    if check and result.returncode != 0:
        if "not permitted to create or approve pull requests" in result.stderr:
            raise SyncError(
                "GitHub won't let this workflow open pull requests. Turn on Settings > "
                "Actions > General > 'Allow GitHub Actions to create and approve pull "
                "requests' (in the organisation's settings first, then the repo's)."
            )
        raise SyncError(f"gh {args[0]} {args[1]} failed:\n{result.stderr}")
    return result


def open_pull_request() -> str | None:
    found = gh("pr", "list", "--head", CHANGES_BRANCH, "--state", "open", "--json", "number")
    numbers = [str(pr["number"]) for pr in json.loads(found.stdout or "[]")]
    return numbers[0] if numbers else None


def deploy_running() -> bool:
    found = gh("run", "list", "--workflow", "deploy.yml", "--limit", "10", "--json", "status")
    return any(r["status"] != "completed" for r in json.loads(found.stdout or "[]"))


# ----------------------------------------------------------------- the three commands


def guard(repo: Path, tmp: Path) -> int:
    if os.environ.get("OVERWRITE_WINDMILL_EDITS") == "true":
        say("Not checking for Windmill edits: the deploy was asked to overwrite them.")
        return 0
    snapshot = read_snapshot(repo, tmp / "snapshot")
    if snapshot is None:
        say(f"No {SNAPSHOT_BRANCH} branch yet (the first deploy makes it): nothing to check.")
        return 0
    windmill = files(pull_windmill(repo, tmp / "windmill"))
    edits = unsynced_edits(windmill, snapshot[0], files(repo))
    if not edits:
        say("OK    Windmill has no edits this deploy would overwrite")
        return 0
    say(
        "::error::Windmill has edits that aren't on GitHub yet, and deploying would overwrite them:"
    )
    for name in edits:
        say(f"        {name}")
    say(
        f"Merge the '{PR_TITLE}' pull request first (it's opened within 15 minutes of an edit; "
        "to open it now, run Actions > windmill to github > Run workflow). Merging it deploys "
        "everything on main, including this commit. If the edits are undone in Windmill "
        "instead, run this deploy again. To overwrite the Windmill edits anyway, run "
        "Actions > deploy > Run workflow with 'Overwrite edits made in Windmill' ticked."
    )
    return 1


def snapshot(repo: Path, tmp: Path) -> int:
    commit = os.environ.get("GITHUB_SHA") or run(["git", "rev-parse", "HEAD"], cwd=repo).stdout
    commit = commit.strip()
    windmill = pull_windmill(repo, tmp / "windmill")
    write_snapshot(repo, windmill, commit, f"Windmill just after deploying {commit[:7]}")
    say(f"OK    Saved what Windmill holds to {SNAPSHOT_BRANCH}")
    return 0


def sync(repo: Path, tmp: Path) -> int:
    if deploy_running():
        say("A deploy is running; trying again next time.")
        return 0
    windmill_root = pull_windmill(repo, tmp / "windmill")
    previous = read_snapshot(repo, tmp / "snapshot")
    if previous is None:
        head = run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        write_snapshot(repo, windmill_root, head, "Starting point: what Windmill holds now")
        say(f"No {SNAPSHOT_BRANCH} branch yet, so saved what Windmill holds as the start point.")
        return 0
    snapshot_files, deployed_commit = previous
    windmill = files(windmill_root)
    number = open_pull_request()

    if not changed(windmill, snapshot_files):
        say("OK    No edits in Windmill since the last deploy")
        if number:
            gh(
                "pr",
                "close",
                number,
                "--delete-branch",
                "--comment",
                "Windmill matches the last deploy again, so there's nothing left to bring over.",
            )
        return 0

    # Files also changed on GitHub since the last deploy (to warn the reviewer).
    run(["git", "fetch", "--depth=1", "origin", deployed_commit], cwd=repo, check=False)
    since = run(
        ["git", "diff", "--name-only", deployed_commit, "HEAD", "--", "f/"], cwd=repo, check=False
    )
    on_github = set(since.stdout.split()) if since.returncode == 0 else set()

    edits = apply_edits(windmill, snapshot_files, repo)
    if not run(["git", "status", "--porcelain", "--", "f/"], cwd=repo).stdout.strip():
        say("OK    Windmill's edits are already on main (waiting to be deployed)")
        if number:
            gh(
                "pr",
                "close",
                number,
                "--delete-branch",
                "--comment",
                "These edits are on main now.",
            )
        return 0

    say("Edits made in Windmill:")
    for name, kind in edits:
        say(f"        {kind}: {name}")
    body = pr_body(edits, who_saved(edits), sorted(n for n, _ in edits if n in on_github))
    (tmp / "body.md").write_text(body, encoding="utf-8")

    run(["git", "checkout", "-B", CHANGES_BRANCH], cwd=repo)
    run(["git", "add", "--all", "--", "f/"], cwd=repo)
    details = "\n".join(f"{kind}: {name}" for name, kind in edits)
    run(["git", *BOT, "commit", "-m", PR_TITLE, "-m", details], cwd=repo)

    # Only push (and re-run the checks) if the branch doesn't already hold exactly this.
    remote = f"refs/remotes/origin/{CHANGES_BRANCH}"
    fetched = run(
        ["git", "fetch", "--depth=1", "origin", f"+refs/heads/{CHANGES_BRANCH}:{remote}"],
        cwd=repo,
        check=False,
    )
    same = (
        number is not None
        and fetched.returncode == 0
        and run(["git", "diff", "--quiet", remote, "HEAD"], cwd=repo, check=False).returncode == 0
    )
    if same:
        say("OK    The pull request already has these edits")
    else:
        run(["git", "push", "--force", "origin", f"HEAD:refs/heads/{CHANGES_BRANCH}"], cwd=repo)
        # Pushes made by a workflow don't start other workflows, so start the checks.
        gh("workflow", "run", "checks.yml", "--ref", CHANGES_BRANCH)
    if number:
        gh("pr", "edit", number, "--body-file", str(tmp / "body.md"))
        say(f"OK    Updated pull request #{number}")
    else:
        created = gh(
            "pr",
            "create",
            "--base",
            "main",
            "--head",
            CHANGES_BRANCH,
            "--title",
            PR_TITLE,
            "--body-file",
            str(tmp / "body.md"),
        )
        say(f"OK    Opened {created.stdout.strip()}")
    return 0


def main(argv: list[str]) -> int:
    commands = {"sync": sync, "guard": guard, "snapshot": snapshot}
    if len(argv) != 2 or argv[1] not in commands:
        print(__doc__)
        return 2
    try:
        with tempfile.TemporaryDirectory() as tmp:
            return commands[argv[1]](Path.cwd(), Path(tmp))
    except SyncError as error:
        say(f"::error::{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
