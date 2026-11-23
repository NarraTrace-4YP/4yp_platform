"""Checks deployment files that must survive Windows and Linux checkouts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_linux_shell_scripts_have_lf_line_endings():
    scripts = sorted(ROOT.glob("server/*.sh"))
    scripts += [
        ROOT / "postgres/20-project.sh",
        ROOT / "postgres/weknora-init.sh",
        ROOT / "mlflow/start.sh",
        ROOT / "local/windmill-bootstrap.sh",
    ]
    assert scripts
    with_crlf = [str(path.relative_to(ROOT)) for path in scripts if b"\r\n" in path.read_bytes()]
    assert not with_crlf, f"Linux shell scripts checked out with CRLF: {with_crlf}"


def test_git_attributes_keep_shell_scripts_on_lf():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "*.sh text eol=lf" in attributes


def test_postgres_health_requires_project_initialization():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "rolname = 'pipeline'" in compose
    assert "to_regnamespace('corpus') IS NOT NULL" in compose


def test_local_windmill_bootstrap_is_profile_gated_and_has_no_github_sync():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    script = (ROOT / "local/windmill-bootstrap.sh").read_text(encoding="utf-8")
    assert 'profiles: ["local-tools"]' in compose
    assert "windmill-bootstrap" in compose
    assert ".:/source:ro" in compose
    assert "cp -R /source/f" in script
    for forbidden in ("GITHUB", "git push", "gh ", "WINDMILL_DEPLOY_TOKEN"):
        assert forbidden not in script


def test_local_and_server_deploys_publish_the_stable_o1_url():
    bootstrap = (ROOT / "local/windmill-bootstrap.sh").read_text(encoding="utf-8")
    server = (ROOT / "server/windmill.sh").read_text(encoding="utf-8")
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    for text in (bootstrap, server, deploy):
        assert '"execution_mode":"anonymous"' in text
        assert "apps/secret_of/" in text
        assert "apps_u/public_app/" in text
    assert "/runtime/o1.json" in (ROOT / "site/o1/index.html").read_text(encoding="utf-8")
    assert "./runtime:/runtime" in (ROOT / "compose.yaml").read_text(encoding="utf-8")
