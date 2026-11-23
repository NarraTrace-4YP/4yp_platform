"""WeKnora's server set-up: pinned images, closed sign-up, its own database, its secrets."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
WEKNORA = {name: service for name, service in SERVICES.items() if name.startswith("weknora-")}


def test_all_weknora_images_are_one_pinned_version():
    versions = {
        service["image"].split(":")[1]
        for service in WEKNORA.values()
        if service["image"].startswith("wechatopenai/")
    }
    assert len(versions) == 1, versions
    assert re.fullmatch(r"v\d+\.\d+\.\d+", versions.pop())


def test_the_init_image_matches_the_shared_postgres():
    assert WEKNORA["weknora-db-init"]["image"] == SERVICES["postgres"]["image"]


def test_weknora_is_reached_only_through_caddy():
    for name, service in WEKNORA.items():
        assert "ports" not in service, f"{name} opens a port on the server"
    caddyfile = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert "weknora.{$DOMAIN}" in caddyfile
    assert "reverse_proxy weknora-frontend:80" in caddyfile
    # Accounts come only from server/team.sh, so the sign-up API is closed to the internet.
    assert "@signup path /api/v1/auth/register*" in caddyfile
    assert re.search(r"respond @signup .* 403", caddyfile)


def test_caddy_does_not_wait_for_weknora():
    # A broken WeKnora must not take Windmill and MLflow down with it.
    assert not any(name.startswith("weknora") for name in SERVICES["caddy"]["depends_on"])


def test_every_weknora_service_has_a_memory_limit():
    for name, service in WEKNORA.items():
        if name != "weknora-db-init":
            assert "mem_limit" in service, name


def test_the_app_uses_its_own_database_and_may_call_only_the_gateway():
    env = WEKNORA["weknora-app"]["environment"]
    assert env["DB_HOST"] == "postgres"
    assert env["DB_USER"] == env["DB_NAME"] == "weknora"
    assert env["SSRF_WHITELIST_EXTRA"] == "llm-gateway"
    assert env["WEKNORA_SANDBOX_DOCKER_ENABLED"] == "false"
    depends = WEKNORA["weknora-app"]["depends_on"]
    assert depends["weknora-db-init"]["condition"] == "service_completed_successfully"


def test_every_secret_the_services_use_is_generated():
    used = set(re.findall(r"\$\{(WEKNORA_[A-Z_]+)", (ROOT / "compose.yaml").read_text()))
    script = (ROOT / "server" / "weknora.sh").read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    local = (ROOT / ".env.local.example").read_text(encoding="utf-8")
    for name in used:
        assert f"{name}=" in example, name
        assert f"{name}=" in local, name
        assert name in script, name
    # WeKnora rejects an AES key that isn't exactly 32 characters (16 bytes as hex).
    assert "WEKNORA_AES_KEY:16" in script
    local_key = re.search(r"^WEKNORA_AES_KEY=(.*)$", local, re.MULTILINE).group(1)
    assert len(local_key) == 32


def test_update_fills_in_settings_before_starting_services():
    update = (ROOT / "server" / "update.sh").read_text(encoding="utf-8")
    assert update.index("weknora.sh env") < update.index("docker compose up")


def test_the_database_script_changes_nothing_once_it_has_run():
    script = (ROOT / "postgres" / "weknora-init.sh").read_text(encoding="utf-8")
    assert "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'weknora')" in script
    assert "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'weknora')" in script
    for extension in ('"uuid-ossp"', "vector", "pg_trgm", "pg_search"):
        assert f"CREATE EXTENSION IF NOT EXISTS {extension};" in script
    assert "REVOKE ALL ON DATABASE weknora FROM PUBLIC" in script


def test_team_sync_gives_everyone_a_weknora_login():
    team = (ROOT / "server" / "team.sh").read_text(encoding="utf-8")
    assert '. "$REPO_DIR/server/weknora.sh"' in team
    assert 'add_weknora_login "$user"' in team
    assert 'wk_disable_login "$user"' in team
