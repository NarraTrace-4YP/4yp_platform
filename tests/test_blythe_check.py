"""Tests for f/dhr/tools/blythe_check.py, which tells a person whether Windmill can use Blythe
as them and whether their ~/narratrace is ready.

Their set-up is built in a local folder standing in for their Blythe home, and the script's
own shell commands run there (LocalBlythe); the login and SLURM are stand-ins.
"""

import subprocess

import paramiko
import pytest
from test_blythe_accounts import variables_for
from test_llm_server import LocalBlythe

from f.dhr import blythe
from f.dhr.tools import blythe_check as tool

MODEL = tool.MODEL


def set_up(home, freeze="vllm==0.34.0\n"):
    """A complete ~/narratrace, as narratrace-slurm's README makes it."""
    base = home / "narratrace"
    code = base / "slurm"
    (code / "jobs").mkdir(parents=True)
    for name in tool.JOB_FILES:
        (code / "jobs" / name).write_text("#!/bin/bash\n")
    git = ["git", "-C", str(code), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-qm", "jobs"], check=True)
    (base / "jobs").symlink_to(code / "jobs")
    share = home / "share"
    for name in tool.SHARE_LINKS:
        (share / name).mkdir(parents=True)
        (base / name).symlink_to(share / name)
    (share / "env" / "bin").mkdir()
    (share / "env" / "bin" / "python").write_text("")
    (share / "env" / "bin" / "python").chmod(0o755)
    (base / "env-freeze.txt").write_text("torch==2.14.0\n" + freeze)
    model = share / "models" / MODEL
    model.mkdir(parents=True)
    (model / "config.json").write_text("{}")
    (model / "NARRATRACE_REVISION").write_text("abc123\n")
    return base


def by_name(checks):
    return {c["check"]: c for c in checks}


def test_a_complete_set_up_passes(tmp_path):
    set_up(tmp_path)
    checks = by_name(tool.check_setup(LocalBlythe(tmp_path)))
    assert all(c["ok"] for c in checks.values()), checks
    assert "vllm==0.34.0" in checks["Python environment"]["detail"]
    assert "abc123" in checks["Model"]["detail"]


def test_each_missing_piece_is_named_with_its_fix(tmp_path):
    base = set_up(tmp_path, freeze="")
    (base / "slurm" / "stray.txt").write_text("x")  # a change made on Blythe
    (base / "jobs" / "llm_serve.sbatch").unlink()
    (base / "cache").unlink()
    (base / "cache").mkdir()  # a real folder in the small home folder, not a link
    (tmp_path / "share" / "models" / MODEL / "config.json").unlink()
    checks = by_name(tool.check_setup(LocalBlythe(tmp_path)))
    assert (
        not checks["narratrace-slurm"]["ok"]
        and "changed file" in checks["narratrace-slurm"]["detail"]
    )
    assert not checks["Job files"]["ok"] and "llm_serve.sbatch" in checks["Job files"]["detail"]
    assert (
        not checks["Share folder links"]["ok"] and "cache" in checks["Share folder links"]["detail"]
    )
    assert not checks["Python environment"]["ok"] and not checks["Model"]["ok"]
    assert all(c.get("fix") for c in checks.values())


def test_nothing_set_up_at_all(tmp_path):
    checks = by_name(tool.check_setup(LocalBlythe(tmp_path)))
    assert not any(c["ok"] for c in checks.values())
    assert "isn't there" in checks["narratrace-slurm"]["detail"]


def test_the_windmill_variables():
    get_variable, _, _ = variables_for("ks0610")
    account, (good,) = tool.check_variables("ks0610", get_variable)
    assert good["ok"] and account.username == "csabcd" and "csabcd" in good["detail"]
    get_variable, _, _ = variables_for("ks0610", blythe_totp_secret=None)
    account, (bad,) = tool.check_variables("ks0610", get_variable)
    assert account is None and not bad["ok"]
    assert "blythe_totp_secret" in bad["detail"] and "u/ks0610/blythe_username" in bad["fix"]


@pytest.mark.parametrize(
    "failure, says",
    [
        (blythe.TwoFactorRefused("Authentication failed."), "2FA"),
        (blythe.BlytheError("Blythe's host key SHA256:x isn't a known one."), "host key"),
        (paramiko.AuthenticationException("Authentication failed."), "authorized_keys"),
        (TimeoutError("timed out"), "timeout"),
    ],
)
def test_each_way_the_login_can_fail_says_what_to_do(monkeypatch, failure, says):
    def connect(self):
        raise failure

    monkeypatch.setattr(blythe.Blythe, "connect", connect)
    b, result = tool.log_in(object())
    assert b is None and not result["ok"] and says.lower() in result["fix"].lower()


class SlurmStandIn(LocalBlythe):
    def slurm(self, command):
        assert command.startswith("squeue -h -u")
        return 0, "1381794 narratrace-serve RUNNING 3:19\n", ""

    def close(self):
        pass


def test_the_whole_check(tmp_path, monkeypatch):
    set_up(tmp_path)
    get_variable, _, _ = variables_for("ks0610")
    monkeypatch.setattr(
        tool, "load_account", lambda user, _=None: blythe.load_account(user, get_variable)
    )
    monkeypatch.setattr(blythe.Blythe, "connect", lambda self: SlurmStandIn(tmp_path))
    monkeypatch.setenv("WM_USERNAME", "ks0610")
    result = tool.main()
    assert result["result"].startswith("All good"), result
    names = [c["check"] for c in result["checks"]]
    assert names == [
        "Windmill variables",
        "Login",
        "narratrace-slurm",
        "Job files",
        "Share folder links",
        "Python environment",
        "Model",
        "SLURM",
    ]
    assert "1 job(s)" in result["checks"][-1]["detail"]

    monkeypatch.setattr(blythe.Blythe, "connect", lambda self: pytest.fail("offline logged in"))
    offline = tool.main(offline=True)
    assert [c["check"] for c in offline["checks"]] == ["Windmill variables"]
    assert "wasn't contacted" in offline["result"]


def test_someone_without_variables_is_told_before_any_login(monkeypatch):
    get_variable, _, _ = variables_for("markcoding05", blythe_ssh_key=None)
    monkeypatch.setattr(
        tool, "load_account", lambda user, _=None: blythe.load_account(user, get_variable)
    )
    monkeypatch.setattr(blythe.Blythe, "connect", lambda self: pytest.fail("logged in anyway"))
    monkeypatch.setenv("WM_USERNAME", "markcoding05")
    result = tool.main()
    assert result["result"].startswith("Not ready: Windmill variables")
