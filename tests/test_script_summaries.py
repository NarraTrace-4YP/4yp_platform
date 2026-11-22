"""The scripts' summaries in Windmill come from their docstrings (.github/script_summaries.py).

Every Python file in f/dhr/O1 starts its docstring with what it is and which stage it
belongs to, so Windmill's script list reads the same way for every stage:

    O1 stage N: ...          the stage's pipeline script
    O1 stage N, app: ...     reads the stage's results for the O1 app
    O1 stage N, shared: ...  code the stage's scripts import
    O1 stages 1-5, app: ...  the app's status, across every stage
"""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "script_summaries", ROOT / ".github" / "script_summaries.py"
)
summaries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summaries)

STAGES = {  # each stage folder's number, as the O1 app and f/dhr/README.md number them
    "load_reviews": "stage 1",
    "link_case_metadata": "stage 2",
    "group_blocks_by_section": "stage 3",
    "find_candidate_template_sections": "stage 4",
    "choose_template_section": "stage 5",
    "app_backend": "stages 1-5",
}
O1_FILES = sorted(
    p for p in (ROOT / "f" / "dhr" / "O1").rglob("*.py") if "__pycache__" not in p.parts
)
PATTERN = re.compile(r"^O1 (stage [1-5]|stages 1-5)(, (app|shared))?: [a-zA-Z(]")


@pytest.mark.parametrize("path", O1_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_o1_script_says_its_stage_first(path):
    summary = summaries.summary_of(path)
    assert summary, f"{path.name} has no docstring"
    assert PATTERN.match(summary), f"{path.name}: {summary!r} doesn't start 'O1 stage N: '"
    folder = path.parent.name
    assert summary.startswith(f"O1 {STAGES[folder]}"), f"{path.name} is in {folder}"
    has_main = "def main(" in path.read_text(encoding="utf-8")
    kind = "app" if path.name.startswith("get_") else "pipeline" if has_main else "shared"
    expected = {"app": ", app: ", "shared": ", shared: ", "pipeline": f"{STAGES[folder]}: "}
    assert expected[kind] in summary, f"{path.name} is a {kind} script: {summary!r}"
    assert len(summary) <= 95, "keep it to one line"


def test_every_stage_has_its_pipeline_script():
    pipelines = {
        summaries.summary_of(p).split(":")[0]
        for p in O1_FILES
        if ", " not in summaries.summary_of(p).split(":")[0]
    }
    assert pipelines == {f"O1 stage {n}" for n in range(1, 6)}


def test_summary_is_the_docstrings_first_line(tmp_path):
    script = tmp_path / "x.py"
    script.write_text('# /// script\n# ///\n"""O1 stage 3: do a thing.\n\nMore.\n"""\n')
    assert summaries.summary_of(script) == "O1 stage 3: do a thing"
    (tmp_path / "y.py").write_text("x = 1\n")
    assert summaries.summary_of(tmp_path / "y.py") is None


def test_the_generated_metadata_gets_the_summary():
    generated = "summary: ''\ndescription: ''\nlock: '!inline x.script.lock'\nkind: script\n"
    out = summaries.set_summary(generated, 'O1 stage 4, app: "scores" — and more')
    assert out.splitlines()[0] == 'summary: "O1 stage 4, app: \\"scores\\" — and more"'
    assert out.splitlines()[1:] == generated.splitlines()[1:]
    assert summaries.set_summary("kind: script\n", "S").startswith('summary: "S"\n')
    # only the top-level key: a schema property called summary is left alone
    nested = "summary: ''\nschema:\n  properties:\n    summary:\n      type: string\n"
    assert summaries.set_summary(nested, "S").count('"S"') == 1


def test_the_deploy_sets_summaries_between_generating_and_pushing():
    deploy = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    generate = deploy.index("wmill generate-metadata")
    summaries_step = deploy.index("python3 .github/script_summaries.py")
    push = deploy.index("wmill sync push")
    assert generate < summaries_step < push
