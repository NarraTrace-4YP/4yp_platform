"""The LLM gateway's server set-up: its image, its packages, and that it is never public."""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCKED = {
    package["name"]: package["version"]
    for package in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]
}


def test_the_image_uses_the_locked_package_versions():
    pins = {}
    for line in (ROOT / "gateway" / "requirements.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, version = line.split("==")
            pins[name.strip()] = version.strip()
    assert set(pins) == {"fastapi", "uvicorn", "httpx", "paramiko", "pyotp"}
    for name, version in pins.items():
        assert LOCKED[name] == version, (
            f"{name} is {version} in the image but {LOCKED[name]} in uv.lock"
        )


def test_the_gateway_is_only_reachable_inside_the_server():
    service = yaml.safe_load((ROOT / "compose.yaml").read_text())["services"]["llm-gateway"]
    assert service["ports"] == ["127.0.0.1:${LLM_GATEWAY_HOST_PORT:-8100}:8000"]
    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text()
    assert "llm-gateway" not in caddyfile  # nothing on the website points at it


def test_the_image_gets_only_what_it_copies():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ignore[ignore.index("*") :] == ["*", "!gateway/", "!f/dhr/blythe.py"]
    dockerfile = (ROOT / "gateway" / "Dockerfile").read_text()
    for source in re.findall(r"^COPY (\S+) ", dockerfile, re.MULTILINE):
        assert (ROOT / source).exists(), source
    assert '--workers", "1"' in dockerfile  # sessions live in one process's memory
