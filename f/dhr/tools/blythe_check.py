# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "paramiko==5.1.0",
#     "pyotp==2.12.1",
#     "wmill==1.875.0",
# ]
# ///
"""Tool: check your Blythe set-up.

Run it in Windmill as yourself (it uses your own account, like everything on Blythe). It
checks, in order:
  1. your Windmill variables (u/<you>/blythe_username, blythe_ssh_key, blythe_key_passphrase,
     blythe_totp_secret): there, the key opens with the passphrase, the 2FA secret works;
  2. the login: Blythe's host key is the known one, and it accepts your key and 2FA code;
  3. your ~/narratrace on Blythe: the narratrace-slurm copy, the job files, the Python
     environment with vLLM, the model, and the links to your share folder;
  4. SLURM: that it answers, and what you have queued.
Each check says what is wrong and how to fix it. It only looks: nothing on Blythe is changed
and no job is submitted. offline=True does only step 1, without contacting Blythe.
"""

from __future__ import annotations

from ..blythe import (
    KEY_VARIABLE,
    PASSPHRASE_VARIABLE,
    TOTP_VARIABLE,
    USERNAME_VARIABLE,
    Blythe,
    BlytheError,
    TwoFactorRefused,
    load_account,
    q,
    windmill_user,
)
from ..llm import MODEL

JOB_FILES = ("llm_batch.sbatch", "llm_serve.sbatch", "test_gpu.sbatch")
SHARE_LINKS = ("env", "models", "hf-home", "uv-cache", "vllm-cache", "python", "cache")
SETUP_HELP = "Set up ~/narratrace as narratrace-slurm's README says, logged in to Blythe as you."


def check(name: str, ok: bool, detail: str, fix: str | None = None) -> dict:
    return {"check": name, "ok": ok, "detail": detail, **({"fix": fix} if not ok and fix else {})}


def check_variables(user: str, get_variable=None):
    """Step 1: the person's own Windmill variables. Returns (their account or None, checks)."""
    try:
        account = load_account(user, get_variable)
    except BlytheError as error:
        names = ", ".join(
            f"u/{user}/{n}"
            for n in (USERNAME_VARIABLE, KEY_VARIABLE, PASSPHRASE_VARIABLE, TOTP_VARIABLE)
        )
        return None, [
            check(
                "Windmill variables",
                False,
                str(error),
                f"In Windmill (Resources > Variables), make {names} under your own folder,"
                " each ticked secret (f/dhr/README.md).",
            )
        ]
    return account, [
        check(
            "Windmill variables",
            True,
            f"Found yours; the key opens, the 2FA secret makes codes, Blythe login"
            f" {account.username}.",
        )
    ]


def log_in(account) -> tuple[Blythe | None, dict]:
    """Step 2: the login itself, with what to do about each way it can fail."""
    try:
        b = Blythe(account).connect()
    except TwoFactorRefused as error:
        return None, check(
            "Login",
            False,
            f"Blythe accepted your key but refused the 2FA code ({error}).",
            "Check your 2FA secret, u/<you>/blythe_totp_secret, is the one SCRTP set up for your"
            " account (the same one your phone's authenticator uses), then run this again a"
            " minute later.",
        )
    except BlytheError as error:
        return None, check(
            "Login",
            False,
            str(error),
            "If it's about the host key, tell Rowan: Blythe may have changed its keys. If it"
            " asked for something else, SCRTP may not have set your account up for key + 2FA.",
        )
    except Exception as error:  # noqa: BLE001 - paramiko's own errors, e.g. key refused
        return None, check(
            "Login",
            False,
            f"{type(error).__name__}: {error}",
            "If the key was refused: its public half must be in ~/.ssh/authorized_keys on"
            " Blythe, and SCRTP must have agreed to it being used from the project server."
            " A timeout means Blythe couldn't be reached: try again later.",
        )
    status, out, _ = b.sh("hostname; whoami")
    host, who = (out.split() + ["?", "?"])[:2]
    return b, check("Login", True, f"Logged in to {host} as {who}; home folder {b.home}.")


def check_setup(b) -> list[dict]:
    """Step 3: ~/narratrace, read with fixed commands only."""
    checks = []
    base = b.path()
    code = b.path("slurm")
    status, out, _ = b.sh(
        f"git -C {q(code)} rev-parse --short HEAD && git -C {q(code)} status --porcelain | wc -l"
    )
    lines = out.split()
    if status == 0 and len(lines) >= 2:
        dirty = int(lines[1]) if lines[1].isdigit() else 0
        checks.append(
            check(
                "narratrace-slurm",
                dirty == 0,
                f"~/narratrace/slurm is at commit {lines[0]}"
                + (f", with {dirty} changed file(s)" if dirty else ""),
                "Undo local changes there (git -C ~/narratrace/slurm status), so what runs is"
                " what was reviewed, then git pull.",
            )
        )
    else:
        checks.append(
            check("narratrace-slurm", False, "~/narratrace/slurm isn't there.", SETUP_HELP)
        )

    jobs = b.path("jobs")
    status, out, _ = b.sh(f"ls {q(jobs)}/ 2>/dev/null")
    missing = [name for name in JOB_FILES if name not in out.split()]
    checks.append(
        check(
            "Job files",
            not missing,
            "~/narratrace/jobs has " + (", ".join(JOB_FILES) if not missing else f"no {missing}"),
            "Link them (ln -s ~/narratrace/slurm/jobs ~/narratrace/jobs) and git pull in"
            " ~/narratrace/slurm to get the newest job files.",
        )
    )

    status, out, _ = b.sh(
        "for d in " + " ".join(SHARE_LINKS) + f"; do test -L {q(base)}/$d || echo $d; done"
    )
    unlinked = out.split()
    checks.append(
        check(
            "Share folder links",
            not unlinked,
            "The big folders are links to your share folder"
            if not unlinked
            else f"Not links to your share folder: {unlinked}",
            "Make them as narratrace-slurm's README shows, so the home folder (2 GB) doesn't fill.",
        )
    )

    status, out, _ = b.sh(
        f"test -x {q(base)}/env/bin/python && grep -i '^vllm==' {q(base)}/env-freeze.txt"
    )
    checks.append(
        check(
            "Python environment",
            status == 0 and out.strip() != "",
            f"~/narratrace/env has {out.strip()}" if status == 0 else "No environment with vLLM.",
            "Run sbatch ~/narratrace/jobs/setup_env.sbatch on Blythe (it takes a while).",
        )
    )

    model = b.path("models", MODEL)
    status, out, _ = b.sh(
        f"test -f {q(model)}/config.json && cat {q(model)}/NARRATRACE_REVISION 2>/dev/null"
    )
    checks.append(
        check(
            "Model",
            status == 0,
            f"{MODEL} is downloaded (commit {out.strip() or 'unknown'})"
            if status == 0
            else f"{MODEL} isn't downloaded.",
            "setup_env.sbatch downloads it; run it, then check its setup-<job>.out.",
        )
    )
    return checks


def check_slurm(b) -> dict:
    """Step 4: SLURM answers, and the person's own queued jobs."""
    status, out, err = b.slurm('squeue -h -u "$USER" -o "%i %j %T %M"')
    if status != 0:
        return check(
            "SLURM",
            False,
            (err or out).strip()[:500] or "squeue failed",
            "SLURM should answer on the login node; if it doesn't, try later or ask SCRTP.",
        )
    jobs = [line for line in out.splitlines() if line.strip()]
    return check(
        "SLURM",
        True,
        f"SLURM answers; you have {len(jobs)} job(s) queued or running"
        + (": " + "; ".join(jobs[:10]) if jobs else ""),
    )


def main(offline: bool = False):
    """offline: only check your Windmill variables, without contacting Blythe."""
    user = windmill_user()
    account, checks = check_variables(user)
    if account and not offline:
        b, login = log_in(account)
        checks.append(login)
        if b is not None:
            try:
                checks += check_setup(b)
                checks.append(check_slurm(b))
            finally:
                b.close()
    problems = [c["check"] for c in checks if not c["ok"]]
    if problems:
        result = "Not ready: " + ", ".join(problems) + ". See each check's fix."
    elif offline:
        result = "Your Windmill variables are fine (Blythe wasn't contacted)."
    else:
        result = "All good: Windmill can use Blythe as you, and your set-up is ready."
    return {"user": user, "result": result, "checks": checks}
