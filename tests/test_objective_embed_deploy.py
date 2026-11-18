"""Deployment must detect when an app rename leaves the website's share URL stale."""

import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "check_objective_embed", ROOT / ".github/check_objective_embed.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.mark.parametrize("matches", [True, False])
def test_deploy_checks_the_share_url_served_by_the_website(monkeypatch, matches):
    expected = "https://windmill.example.com/public/dhr/new-share"
    configured = expected if matches else "https://windmill.example.com/public/dhr/deleted-app"
    requests = []

    def fetch(request, timeout):
        requests.append(request.full_url)
        return io.BytesIO(json.dumps({"url": configured}).encode("utf-8"))

    monkeypatch.setattr(checker.urllib.request, "urlopen", fetch)
    if matches:
        checker.check_objective_embed("https://windmill.example.com/", expected)
    else:
        with pytest.raises(RuntimeError, match="embed URL is stale"):
            checker.check_objective_embed("https://windmill.example.com/", expected)
    assert requests == ["https://example.com/runtime/o1.json"]


def test_stale_embed_check_runs_after_the_deployment_snapshot():
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert workflow.index("windmill_sync.py snapshot") < workflow.index(
        "check_objective_embed.py --windmill-url"
    )
