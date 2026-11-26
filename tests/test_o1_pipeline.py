"""Checks the O1 pipeline (f/dhr/O1/pipeline.flow) and the O1 app (f/dhr/O1/app.raw_app) fit the
scripts they run. Windmill only finds a mismatch when someone presses Run, so these catch a
renamed script, a renamed argument or a misspelt dataset before it is merged."""

import ast
import importlib
import re
import typing
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FLOW = yaml.safe_load((ROOT / "f/dhr/O1/pipeline.flow/flow.yaml").read_text(encoding="utf-8"))
STEPS = FLOW["value"]["modules"]
APP = ROOT / "f/dhr/O1/app.raw_app"
RUNNABLES = {
    path.stem: yaml.safe_load(path.read_text(encoding="utf-8"))
    for path in sorted((APP / "backend").glob("*.yaml"))
}


def script_main(windmill_path: str):
    """The main function of a script in f/, e.g. f/dhr/O1/load_reviews/load_reviews."""
    module = importlib.import_module(windmill_path.replace("/", "."))
    return module.main


def parameters(windmill_path: str) -> dict[str, bool]:
    """main's parameters: {name: has a default}."""
    path = ROOT / f"{windmill_path}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    names = [a.arg for a in main.args.args]
    n_defaults = len(main.args.defaults)
    return {name: i >= len(names) - n_defaults for i, name in enumerate(names)}


@pytest.mark.parametrize("step", STEPS, ids=lambda s: s["id"])
def test_flow_steps_run_scripts_that_exist(step):
    value = step["value"]
    assert value["type"] == "script"
    assert (ROOT / f"{value['path']}.py").is_file(), f"{step['id']}: no script {value['path']}"
    assert "summary" in step, f"{step['id']}: give the step a summary (shown in Windmill)"


@pytest.mark.parametrize("step", STEPS, ids=lambda s: s["id"])
def test_flow_steps_pass_the_arguments_their_script_takes(step):
    value = step["value"]
    params = parameters(value["path"])
    given = set(value["input_transforms"])
    assert given <= set(params), f"{step['id']}: {value['path']} has no {given - set(params)}"
    needed = {name for name, has_default in params.items() if not has_default}
    assert needed <= given, f"{step['id']}: {value['path']} also needs {needed - given}"


@pytest.mark.parametrize("step", STEPS, ids=lambda s: s["id"])
def test_flow_fixed_values_are_allowed(step):
    value = step["value"]
    hints = typing.get_type_hints(script_main(value["path"]))
    for name, transform in value["input_transforms"].items():
        if transform["type"] != "static":
            continue
        hint = hints.get(name)
        if typing.get_origin(hint) is typing.Literal:
            assert transform["value"] in typing.get_args(hint), f"{step['id']}: bad {name}"
        elif hint in (bool, int, str):
            # YAML reads 1 as an int, which Python accepts where a float is wanted.
            assert isinstance(transform["value"], hint), f"{step['id']}: {name} isn't a {hint}"


def test_flow_only_uses_inputs_it_declares():
    declared = set(FLOW["schema"]["properties"])
    assert FLOW["schema"]["order"] == list(FLOW["schema"]["properties"])
    used = set()
    for step in STEPS:
        expressions = [step.get("skip_if", {}).get("expr", "")]
        expressions += [t.get("expr", "") for t in step["value"]["input_transforms"].values()]
        for expression in expressions:
            used |= set(re.findall(r"flow_input\.(\w+)", expression))
    assert used <= declared, f"the flow uses inputs it doesn't declare: {used - declared}"
    assert declared <= used, f"the flow declares inputs it never uses: {declared - used}"


@pytest.mark.parametrize("name", sorted(RUNNABLES))
def test_app_runnables_exist(name):
    runnable = RUNNABLES[name]
    if runnable["type"] == "script":
        assert (ROOT / f"{runnable['path']}.py").is_file(), f"{name}: no {runnable['path']}"
        parameters(runnable["path"])  # fails if the script has no main
    else:
        assert runnable["type"] == "flow"
        assert (ROOT / f"{runnable['path']}.flow" / "flow.yaml").is_file()


def test_frontend_backend_calls_have_bindings():
    """A renamed YAML binding must also be renamed in frontend calls."""
    called = set()
    for path in [*APP.glob("*.ts"), *APP.glob("*.tsx")]:
        called.update(
            re.findall(r"backend(?:Async)?\s*\.\s*(\w+)\s*\(", path.read_text(encoding="utf-8"))
        )
    assert called == set(RUNNABLES)

PUBLIC_RUN_ALLOWED = {"f/dhr/O2/run_analysis/run_analysis"}
def test_public_apps_cannot_start_runs():
    """Anyone with a public app's link can call its backend runnables without logging in.

    So a public app may only list read-only get_* scripts: never a flow, and never a script
    that changes data or starts work (such as a job on Blythe). Those are run in Windmill.
    """
    apps = [
        path.parent
        for path in sorted((ROOT / "f").rglob("raw_app.yaml"))
        if yaml.safe_load(path.read_text(encoding="utf-8")).get("public")
    ]
    assert APP in apps, "the O1 app is embedded on the landing page, so it is public"
    for app in apps:
        for binding in sorted((app / "backend").glob("*.yaml")):
            runnable = yaml.safe_load(binding.read_text(encoding="utf-8"))
            where = binding.relative_to(ROOT)
            assert runnable.get("type") == "script", f"{where}: a public app can't bind a flow"
            if runnable["path"] in PUBLIC_RUN_ALLOWED:
                continue
            assert runnable["path"].rsplit("/", 1)[-1].startswith("get_"), (
                f"{where}: {runnable['path']} isn't a read-only get_* script"
            )
