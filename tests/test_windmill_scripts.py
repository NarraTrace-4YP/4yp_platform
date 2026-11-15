"""Checks the Windmill scripts in f/ follow the rules that keep them working in Windmill.

- A script Windmill runs (one with a main function) starts with a dependency block, and
  every package in it is pinned to the version in uv.lock. That way the code runs with the
  same packages in Windmill as on everyone's laptop.
- main's defaults are plain values, because Windmill builds the run form from them.
- Scripts import each other as `from .name import thing`, the form Windmill can follow.
- Database migrations are numbered in order and never share a name.
- The server, the command-line tool and the scripts' wmill package are all the same
  Windmill version.
"""

import ast
import re
import tomllib
from pathlib import Path

import pytest

from f.dhr.migrations import MIGRATIONS

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = sorted((ROOT / "f").rglob("*.py"))
RUNNABLE = [p for p in SCRIPTS if re.search(r"(?m)^def main\(", p.read_text(encoding="utf-8"))]
LOCKED = {
    package["name"]: package["version"]
    for package in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
}


def dependency_block(path: Path) -> dict:
    """The script's `# /// script` block, read the way Windmill reads it."""
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "# /// script", f"{path.name} must start with '# /// script'"
    end = lines.index("# ///")
    return tomllib.loads("\n".join(line[1:] for line in lines[1:end]))


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_there_are_scripts():
    assert {p.stem for p in RUNNABLE} >= {"load_dhrc", "collect_case_metadata"}


@pytest.mark.parametrize("path", RUNNABLE, ids=lambda p: p.stem)
def test_runnable_scripts_pin_their_packages_to_uv_lock(path):
    block = dependency_block(path)
    assert block["requires-python"] == ">=3.12,<3.13"
    for requirement in block["dependencies"]:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(\[[^\]]+\])?==(\S+)", requirement)
        assert match, f"{path.name}: pin {requirement!r} to an exact version (name==x.y.z)"
        name, version = normalise(match.group(1)), match.group(3)
        if name == "wmill":  # only used inside Windmill, so not in uv.lock
            continue
        assert LOCKED.get(name) == version, (
            f"{path.name}: {name} is {version} here but {LOCKED.get(name)} in uv.lock"
        )


@pytest.mark.parametrize("path", RUNNABLE, ids=lambda p: p.stem)
def test_main_defaults_are_written_out(path):
    """Windmill builds the run form from main's defaults, but only reads literal values."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    for default in main.args.defaults:
        assert isinstance(default, ast.Constant), (
            f'{path.name}: give main\'s defaults as plain values (like 100 or "main"), '
            f"not names (line {default.lineno})"
        )


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.stem)
def test_scripts_import_each_other_by_name(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            assert node.module, (
                f"{path.name}: write `from .name import x`, not `from . import name`"
            )


def test_migrations_are_numbered_in_order():
    names = [name for name, _ in MIGRATIONS]
    assert len(names) == len(set(names))
    numbers = [int(name.split("_", 1)[0]) for name in names]
    assert numbers == list(range(1, len(names) + 1))
    assert all(re.fullmatch(r"\d{3}_[a-z0-9_]+", name) for name in names)


def test_windmill_versions_match():
    files = ["compose.yaml", ".github/workflows/checks.yml", ".github/workflows/deploy.yml"]
    texts = [(ROOT / name).read_text(encoding="utf-8") for name in files]
    texts += [path.read_text(encoding="utf-8") for path in RUNNABLE]
    versions = set()
    for text in texts:
        versions |= set(re.findall(r"windmill(?:-extra|-cli)?[:@](\d+\.\d+\.\d+)", text))
        versions |= set(re.findall(r'"wmill==(\d+\.\d+\.\d+)"', text))
    assert len(versions) == 1, f"Windmill versions differ: {sorted(versions)}"
