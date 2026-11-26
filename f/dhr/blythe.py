"""Running fixed SLURM jobs on Blythe, the university's GPU cluster, from Windmill.

Each run uses the Blythe account of the person who started it. Windmill logs in to Blythe
over SSH as them, with their key and a 2FA code made from their 2FA secret, which they keep
as their own Windmill variables (only their runs, and workspace admins, can read them):
    u/<their Windmill name>/blythe_username         their Blythe login (e.g. csujxb)
    u/<their Windmill name>/blythe_ssh_key          the private key allowed on the server
    u/<their Windmill name>/blythe_key_passphrase   its passphrase
    u/<their Windmill name>/blythe_totp_secret      their 2FA secret (or its otpauth:// link)
A run whose person hasn't set these up stops before contacting Blythe. SCRTP allowed this
for Rowan's account on 19 Nov 2026, until they offer a web interface; each person needs the
same agreement for their own account, and their own ~/narratrace set up on Blythe.

On Blythe everything sits in ~/narratrace (see the README of the separate repository
NarraTrace-4YP/narratrace-slurm, cloned there as ~/narratrace/slurm):
    jobs/<name>.sbatch   the fixed job files, from that repository's jobs/
    runs/<run_id>/       one folder per run: input/ (uploaded here), output/ (written by
                         the job), slurm-<job>.out, and callback_url
Windmill only ever chooses a job file by name (JOBS) and a time limit: it never sends
commands of its own to run on Blythe.

callback_url holds a one-time Windmill address that wakes the waiting run when the job
ends (each job file calls it on exit). Whether the job has really finished is always
decided by asking SLURM (job_state), never by the call.

The llm_serve job keeps a model loaded and answers prompts added while it runs. Its run
folder is a mailbox (narratrace-slurm's llm_serve.py describes the files): start_server
submits it, add_request puts a request's prompts in inbox/, mailbox says what is waiting
and answered, collect downloads a request's answers, and stop_server asks it to stop. A
request is data only (prompts and their labels); the job refuses anything else.

Blythe's login node closes SFTP sessions, so files go over the SSH command channel
(cat / head / find). Every command runs on its own channel of one login, so one task logs
in, and uses one 2FA code, only once. A code works only once, so a refused code is retried
once with the next one. Each account has its own codes, so two people's runs never clash.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import io
import json
import os
import posixpath
import re
import shlex
import socket
import time
from urllib.parse import parse_qs, urlparse

HOST = "blythe.scrtp.warwick.ac.uk"
BASE = "narratrace"  # in the home folder on Blythe

# Blythe's host keys, from Rowan's known_hosts on the project server (19 Nov 2026). A login
# stops before sending anything if Blythe shows another key.
KNOWN_HOST_KEYS = {
    "SHA256:4H8j81Qj1P4A1k33JcUGlr4pT82hSradiUt8khVX/j4",  # RSA
    "SHA256:TPNsZGtGuRZsWBZDjjdwyJBb1zEXPfokFNI6MsFckls",  # ECDSA
    "SHA256:e8tZHZ5vktblxUSX+pxLYLPlmX1Pa6qNuXMAxb3BqAo",  # ED25519
}
# Each person's Windmill variables, under u/<their Windmill name>/ (see above).
USERNAME_VARIABLE = "blythe_username"
KEY_VARIABLE = "blythe_ssh_key"
PASSPHRASE_VARIABLE = "blythe_key_passphrase"
TOTP_VARIABLE = "blythe_totp_secret"
# Blythe logins known before blythe_username existed; the variable wins when it is set.
KNOWN_USERNAMES = {"rshields2004": "csujxb"}
WINDMILL_USER = re.compile(r"^[A-Za-z0-9_]{1,64}$")
BLYTHE_USER = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

# The job files Windmill may submit: ~/narratrace/jobs/<name>.sbatch on Blythe.
JOBS = {"test_gpu", "llm_batch", "llm_serve"}
MAX_HOURS = 48  # the gpu partition's limit

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
# An llm_serve request's name; the job takes only inbox/<request>.jsonl.gz files named so.
REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
ACTIVE_STATES = {
    "PENDING",
    "RUNNING",
    "CONFIGURING",
    "COMPLETING",
    "SUSPENDED",
    "REQUEUED",
    "REQUEUE_HOLD",
    "REQUEUE_FED",
    "RESIZING",
    "SIGNALING",
    "STAGE_OUT",
    "STOPPED",
}
q = shlex.quote


class BlytheError(RuntimeError):
    """Blythe refused something, or answered in a way that can't be used."""


# ---------------------------------------------------------------- checks


def windmill_user() -> str:
    """Who started this run (Windmill names them in WM_USERNAME)."""
    user = os.environ.get("WM_USERNAME") or ""
    if not WINDMILL_USER.match(user):
        raise BlytheError(
            "This run has no Windmill user, so it can't use anyone's Blythe account."
            " Run it from Windmill, logged in as yourself."
        )
    return user


def check_owner() -> str:
    """Stops a run that has no Windmill user to tie it to a Blythe account; returns the user.
    Their Blythe secrets are read (and must exist) when the run logs in (load_account)."""
    return windmill_user()


def check_job(job: str) -> None:
    if job not in JOBS:
        raise BlytheError(f"{job!r} is not one of the job files Windmill may run: {sorted(JOBS)}")


def check_name(name: str) -> None:
    """Run folder and file names: letters, digits, '.', '_' and '-', so they're safe in paths
    and commands."""
    if not SAFE_NAME.match(name):
        raise BlytheError(f"Unsafe name {name!r}: use letters, digits, '.', '_' and '-'.")


def check_request_id(request_id: str) -> None:
    if not isinstance(request_id, str) or not REQUEST_ID.match(request_id):
        raise BlytheError(
            f"Unsafe request name {request_id!r}: 1 to 64 letters, digits, '_' and '-'."
        )


def check_job_id(job_id: str) -> None:
    if not re.fullmatch(r"\d{1,12}", str(job_id)):
        raise BlytheError(f"Not a SLURM job number: {job_id!r}")


def time_limit(hours: int | None) -> str | None:
    """sbatch's --time for a whole number of hours (None: the job file's own limit)."""
    if hours is None:
        return None
    if not isinstance(hours, int) or not 1 <= hours <= MAX_HOURS:
        raise BlytheError(f"hours must be a whole number from 1 to {MAX_HOURS}, not {hours!r}")
    return f"{hours:02d}:00:00"


GPUS_PER_NODE = 3  # Blythe's L40 nodes (sea001-sea016), each with 192 GB of memory
MAX_MEM_GB = 180
CPUS_PER_GPU = 8  # as llm_batch.sbatch asks for one GPU


def resources(gpus: int | None, mem_gb: int | None) -> str:
    """sbatch options for more L40s or memory than the job file asks for ("" for its own)."""
    options = ""
    if gpus is not None:
        if not isinstance(gpus, int) or not 1 <= gpus <= GPUS_PER_NODE:
            raise BlytheError(f"gpus must be 1 to {GPUS_PER_NODE} (one node), not {gpus!r}")
        options += f" --gres=gpu:lovelace_l40:{gpus} --cpus-per-task={CPUS_PER_GPU * gpus}"
    if mem_gb is not None:
        if not isinstance(mem_gb, int) or not 1 <= mem_gb <= MAX_MEM_GB:
            raise BlytheError(f"mem_gb must be 1 to {MAX_MEM_GB}, not {mem_gb!r}")
        cpus = CPUS_PER_GPU * (gpus or 1)
        options += f" --mem-per-cpu={-(-mem_gb * 1024 // cpus)}M"  # the job file uses per-CPU
    return options


def sbatch_command(
    job_file: str,
    run_dir: str,
    hours: int | None = None,
    gpus: int | None = None,
    mem_gb: int | None = None,
) -> str:
    """The sbatch call; its options override the job file's #SBATCH lines."""
    limit = time_limit(hours)
    options = (f" --time={limit}" if limit else "") + resources(gpus, mem_gb)
    return f"sbatch --parsable{options} --chdir {q(run_dir)} {q(job_file)}"


def parse_sbatch(out: str) -> str | None:
    """The job number from `sbatch --parsable` ("1374161" or "1374161;cluster")."""
    ids = [
        m.group(1)
        for m in (re.match(r"^(\d+)(;\S+)?$", line.strip()) for line in out.splitlines())
        if m
    ]
    return ids[-1] if ids else None


def state_command(job_id: str) -> str:
    check_job_id(job_id)
    return (
        f"squeue -h -j {job_id} -o '%T|%r|%M' 2>/dev/null; echo '---'; "
        f"sacct -j {job_id} -X -n -P -o State,ExitCode,Elapsed,NodeList"
    )


def parse_state(out: str) -> dict:
    """What SLURM says about a job, from state_command's output.

    squeue lists a job while it waits or runs; sacct keeps it afterwards. "finished" is True
    only once sacct reports an end state (COMPLETED, FAILED, TIMEOUT, CANCELLED, ...)."""
    queue_part, _, acct_part = out.partition("---")
    queue_line = next((s.strip() for s in queue_part.splitlines() if s.strip()), "")
    acct_line = next((s.strip() for s in acct_part.splitlines() if s.strip()), "")
    queue_state = queue_line.split("|")[0] if queue_line else ""
    if queue_state in ACTIVE_STATES:
        state, reason, elapsed = (queue_line.split("|") + ["", ""])[:3]
        return {"finished": False, "state": state, "reason": reason, "elapsed": elapsed}
    if acct_line:
        state, exit_code, elapsed, node = (acct_line.split("|") + ["", "", ""])[:4]
        finished = bool(state) and state.split()[0] not in ACTIVE_STATES
        return {
            "finished": finished,
            "state": state,
            "exit_code": exit_code,
            "elapsed": elapsed,
            "node": node,
        }
    return {"finished": False, "state": "UNKNOWN (not listed by SLURM yet)"}


def mailbox_command(run_dir: str) -> str:
    """Lists an llm_serve run folder's mailbox in one command (see parse_mailbox)."""
    return (
        f"cd {q(run_dir)} || exit 1; echo '[inbox]'; ls -1 inbox 2>/dev/null; echo '[outbox]';"
        " ls -1 outbox 2>/dev/null; echo '[job]'; cat slurm_job_id 2>/dev/null; echo;"
        " echo '[stop]'; test -e stop && echo yes; echo '[status]'; cat status.json 2>/dev/null"
    )


def parse_mailbox(out: str) -> dict:
    """What mailbox_command printed: the requests waiting in inbox/, those answered or
    refused in outbox/, the SLURM job, whether it was asked to stop, and its status.json."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in out.splitlines():
        if line in ("[inbox]", "[outbox]", "[job]", "[stop]", "[status]"):
            current = sections.setdefault(line[1:-1], [])
        elif current is not None:
            current.append(line)
    if "inbox" not in sections:
        raise BlytheError("That run folder isn't on Blythe.")

    def named(lines: list[str], suffix: str) -> list[str]:
        ids = [line[: -len(suffix)] for line in lines if line.endswith(suffix)]
        return sorted(i for i in ids if REQUEST_ID.match(i))

    outbox = sections.get("outbox", [])
    status_text = "\n".join(sections.get("status", [])).strip()
    try:
        status = json.loads(status_text) if status_text else None
    except ValueError:
        status = None  # the job renames it into place whole, so this is only a safety net
    job = "".join(sections.get("job", [])).strip()
    return {
        "waiting": named(sections["inbox"], ".jsonl.gz"),
        "answered": named(outbox, ".json"),
        "refused": named(outbox, ".error.json"),
        "part_answered": named(outbox, ".partial.jsonl.gz"),
        "slurm_job_id": job if re.fullmatch(r"\d{1,12}", job) else None,
        "stop_asked": "yes" in sections.get("stop", []),
        "status": status,
    }


def events_command(run_dir: str, offset: int) -> str:
    """What an llm_serve job has finished since byte `offset` of its events.log, and its
    status.json, in one command (see parse_events)."""
    return (
        f"cd {q(run_dir)} || exit 1; printf '[events]\\n';"
        f" tail -c +{int(offset) + 1} events.log 2>/dev/null;"
        " printf '\\n[end-events]\\n'; cat status.json 2>/dev/null"
    )


def parse_events(out: bytes, offset: int) -> dict:
    """events_command's output: {"events": the finished or refused requests, "offset": where
    to read from next time (a line still being written is left for then), "status"}."""
    start = b"[events]\n"
    if not out.startswith(start):
        raise BlytheError("That run folder isn't on Blythe.")
    body, _, rest = out[len(start) :].partition(b"\n[end-events]\n")
    whole = body[: body.rfind(b"\n") + 1]
    events = []
    for line in whole.decode(errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    try:
        status = json.loads(rest) if rest.strip() else None
    except ValueError:
        status = None
    return {"events": events, "offset": offset + len(whole), "status": status}


# ---------------------------------------------------------------- secrets


def _clean_key_text(text: str) -> str:
    text = text.strip().replace("\r\n", "\n")
    if "\n" not in text:  # pasted into a one-line box: rebuild the line breaks
        m = re.match(r"^(-----BEGIN [A-Z ]+-----)\s*(.*?)\s*(-----END [A-Z ]+-----)$", text)
        if m:
            text = f"{m.group(1)}\n" + "\n".join(m.group(2).split()) + f"\n{m.group(3)}"
    return text + "\n"


def _load_key(text: str, passphrase: str):
    import paramiko

    errors = []
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(text), password=passphrase or None)
        except Exception as error:  # noqa: BLE001 - try the next key type
            errors.append(f"{cls.__name__}: {error}")
    raise BlytheError("Couldn't open the Blythe SSH key: " + "; ".join(errors))


def fingerprint(key) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def _clean_totp_secret(raw: str) -> str:
    secret = raw.strip()
    if secret.lower().startswith("otpauth://"):
        secret = parse_qs(urlparse(secret).query).get("secret", [""])[0]
    return re.sub(r"[\s-]", "", secret).upper()


@dataclasses.dataclass
class Account:
    """A person's Blythe login: their Windmill name, Blythe username, key and 2FA generator."""

    user: str
    username: str
    key: object
    totp: object


def load_account(user: str | None = None, get_variable=None) -> Account:
    """The Blythe account of the person running this (or of `user`), from their own Windmill
    variables. get_variable reads one variable by path; inside Windmill it is
    wmill.get_variable, which only reads what the run's own user may read."""
    import pyotp

    user = user or windmill_user()
    if not WINDMILL_USER.match(user):
        raise BlytheError(f"Not a Windmill user name: {user!r}")
    if get_variable is None:
        import wmill

        get_variable = wmill.get_variable

    def variable(name: str, required: bool = True) -> str:
        path = f"u/{user}/{name}"
        try:
            value = get_variable(path)
        except Exception:  # noqa: BLE001 - Windmill answers "not found" with an error
            value = None
        if required and not value:
            raise BlytheError(
                f"{user} has no Blythe account set up in Windmill: {path} is missing or empty."
                f" Add your own u/{user}/{USERNAME_VARIABLE}, {KEY_VARIABLE},"
                f" {PASSPHRASE_VARIABLE} and {TOTP_VARIABLE} (see f/dhr/README.md)."
            )
        return value or ""

    username = variable(USERNAME_VARIABLE, required=user not in KNOWN_USERNAMES).strip()
    username = username or KNOWN_USERNAMES[user]
    if not BLYTHE_USER.match(username):
        raise BlytheError(f"u/{user}/{USERNAME_VARIABLE} isn't a Blythe username: {username!r}")
    key = _load_key(
        _clean_key_text(variable(KEY_VARIABLE)), variable(PASSPHRASE_VARIABLE, required=False)
    )
    secret = _clean_totp_secret(variable(TOTP_VARIABLE))
    try:
        totp = pyotp.TOTP(secret)
        totp.now()
    except Exception as error:  # noqa: BLE001 - say which variable is wrong
        raise BlytheError(f"u/{user}/{TOTP_VARIABLE} isn't a valid 2FA secret: {error}") from None
    return Account(user, username, key, totp)


# ---------------------------------------------------------------- the login


class TwoFactorRefused(BlytheError):
    pass


def _as_bytes(data) -> bytes:
    return data if isinstance(data, bytes) else data.encode()


class Blythe:
    """One SSH login to Blythe, as the person running this. Use as `with Blythe() as b:`.

    account: whose login to use (default: load_account(), the run's own user). keepalive:
    seconds between keep-alive messages, for a login held open for a long time (the gateway)."""

    def __init__(self, account: Account | None = None, keepalive: int = 0):
        self.account, self.keepalive = account, keepalive

    def __enter__(self) -> Blythe:
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> Blythe:
        account = self.account = self.account or load_account()
        try:
            self.transport = self._login(account)
        except TwoFactorRefused:
            wait = 31 - int(time.time()) % 30
            print(f"2FA code refused; trying once more with the next code in {wait} s", flush=True)
            time.sleep(wait)
            self.transport = self._login(account)
        if self.keepalive:
            self.transport.set_keepalive(self.keepalive)
        status, out, err = self.sh('echo "$HOME"')
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if status != 0 or not lines or not lines[-1].startswith("/"):
            self.transport.close()
            raise BlytheError(f"Couldn't read the home folder on Blythe: {(err or out).strip()}")
        self.home = lines[-1]
        return self

    def close(self) -> None:
        transport = getattr(self, "transport", None)
        if transport is not None:
            transport.close()

    def alive(self) -> bool:
        transport = getattr(self, "transport", None)
        return bool(transport is not None and transport.is_active())

    @staticmethod
    def _login(account: Account):
        import paramiko

        sock = socket.create_connection((HOST, 22), timeout=15)
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=20)
            seen = fingerprint(transport.get_remote_server_key())
            if seen not in KNOWN_HOST_KEYS:
                raise BlytheError(f"Blythe's host key {seen} isn't a known one. Nothing was sent.")
            key, totp, username = account.key, account.totp, account.username
            remaining = transport.auth_publickey(username, key)
            if not transport.is_authenticated():
                if "keyboard-interactive" not in remaining:
                    raise BlytheError(f"Blythe asked for {remaining} after the key. Stopped.")

                def answer(title, instructions, prompts):
                    return [
                        totp.now()
                        if ("verification code" in p.lower() or "2fa" in p.lower())
                        else ""
                        for p, _echo in prompts
                    ]

                try:
                    transport.auth_interactive(username, answer)
                except paramiko.AuthenticationException as error:
                    raise TwoFactorRefused(str(error)) from error
            return transport
        except BaseException:
            transport.close()
            raise

    # --- commands

    def _exec(self, command: str, stdin: bytes = b"", timeout: int = 600):
        channel = self.transport.open_session(timeout=20)
        try:
            channel.settimeout(timeout)
            channel.exec_command(command)
            if stdin:
                view = memoryview(stdin)
                for start in range(0, len(view), 1 << 20):
                    channel.sendall(view[start : start + (1 << 20)])
            channel.shutdown_write()
            out = _as_bytes(channel.makefile("rb").read())
            err = _as_bytes(channel.makefile_stderr("rb").read())
            return channel.recv_exit_status(), out, err
        finally:
            channel.close()

    def sh(self, command: str, stdin: bytes = b""):
        status, out, err = self._exec(command, stdin)
        return status, out.decode(errors="replace"), err.decode(errors="replace")

    def slurm(self, command: str):
        """SLURM commands run in a login shell, so its PATH is set up."""
        return self.sh("bash -lc " + q(command))

    def must(self, command: str, what: str, stdin: bytes = b"") -> str:
        status, out, err = self.sh(command, stdin)
        if status != 0:
            raise BlytheError(
                f"Couldn't {what} on Blythe (exit {status}): {(err or out).strip()[:1000]}"
            )
        return out

    # --- files

    def path(self, *parts: str) -> str:
        return posixpath.join(self.home, BASE, *parts)

    def run_dir(self, run_id: str) -> str:
        check_name(run_id)
        return self.path("runs", run_id)

    def exists(self, path: str) -> bool:
        return self.sh(f"test -e {q(path)}")[0] == 0

    def write(self, path: str, data: bytes | str, private: bool = False) -> None:
        prefix = "umask 077 && " if private else ""
        self.must(f"{prefix}cat > {q(path)}", f"write {path}", stdin=_as_bytes(data))

    def write_new(self, path: str, data: bytes | str, private: bool = False) -> None:
        """Writes a file that mustn't exist yet under a hidden name, then renames it into
        place, so a job looking at the folder never reads half of it."""
        folder, name = posixpath.split(path)
        temporary = posixpath.join(folder, f".{name}.tmp")
        prefix = "umask 077 && " if private else ""
        self.must(
            f"{prefix}test ! -e {q(path)} && cat > {q(temporary)} && mv {q(temporary)} {q(path)}",
            f"write {path} (it mustn't exist yet)",
            stdin=_as_bytes(data),
        )

    def read(self, path: str, max_bytes: int | None = None) -> bytes:
        command = f"head -c {int(max_bytes)} {q(path)}" if max_bytes else f"cat {q(path)}"
        status, out, err = self._exec(command)
        if status != 0:
            raise BlytheError(
                f"Couldn't read {path} on Blythe: {err.decode(errors='replace').strip()}"
            )
        return out

    def tail(self, path: str, lines: int = 100) -> str:
        status, out, _ = self.sh(f"tail -n {int(lines)} {q(path)}")
        return out if status == 0 else "(no log file)"

    # --- jobs

    def submit(
        self,
        job: str,
        run_id: str,
        files: dict[str, bytes | str],
        callback_url: str | None,
        hours: int | None = None,
        gpus: int | None = None,
        mem_gb: int | None = None,
    ) -> dict:
        """Makes the run folder, uploads files into its input/, leaves the wake-up address,
        and submits the job. The run folder must be new, so runs never mix."""
        check_job(job)
        resources(gpus, mem_gb)  # refuse bad numbers before making anything
        for name in files:
            check_name(name)
        job_file = self.path("jobs", f"{job}.sbatch")
        if not self.exists(job_file):
            raise BlytheError(f"Job file {job_file} isn't on Blythe.")
        run_dir = self.run_dir(run_id)
        input_dir = posixpath.join(run_dir, "input")
        self.must(
            f"mkdir -p {q(self.path('runs'))} && mkdir {q(run_dir)} {q(input_dir)}",
            "make the run folder",
        )
        for name, data in files.items():
            self.write(posixpath.join(input_dir, name), data)
        self.set_callback(run_dir, callback_url)
        status, out, err = self.slurm(sbatch_command(job_file, run_dir, hours, gpus, mem_gb))
        job_id = parse_sbatch(out)
        if status != 0 or not job_id:
            raise BlytheError(f"sbatch failed (exit {status}): {(err or out).strip()[:2000]}")
        print(f"Submitted {job} as SLURM job {job_id} in {run_dir}", flush=True)
        return {"run_dir": run_dir, "slurm_job_id": job_id}

    def set_callback(self, run_dir: str, callback_url: str | None) -> None:
        """Leaves (or, with None, removes) the address the job calls when it ends."""
        path = posixpath.join(run_dir, "callback_url")
        if callback_url is None:
            self.sh(f"rm -f {q(path)}")
        else:
            self.write(path, callback_url, private=True)

    def job_state(self, job_id: str) -> dict:
        _, out, _ = self.slurm(state_command(job_id))
        return parse_state(out)

    def cancel(self, job_id: str) -> dict:
        check_job_id(job_id)
        status, _, err = self.slurm(f"scancel {job_id}")
        return {"exit_status": status, "stderr": err.strip()}

    # --- llm_serve: a model kept loaded, answering prompts added while it runs

    def start_server(
        self,
        run_id: str,
        request: dict,
        hours: int,
        callback_url: str | None = None,
        gpus: int | None = None,
        mem_gb: int | None = None,
    ) -> dict:
        """Submits llm_serve with request.json (the model and its settings), and notes the
        SLURM job in the run folder, so mailbox() can name it. gpus and mem_gb ask for more
        than the job file's one L40 (a model split over several: request["engine"] then sets
        tensor_parallel_size to match)."""
        submitted = self.submit(
            "llm_serve",
            run_id,
            {"request.json": json.dumps(request, indent=2)},
            callback_url,
            hours=hours,
            gpus=gpus,
            mem_gb=mem_gb,
        )
        self.write(posixpath.join(submitted["run_dir"], "slurm_job_id"), submitted["slurm_job_id"])
        return submitted

    def mailbox(self, run_id: str) -> dict:
        _, out, _ = self.sh(mailbox_command(self.run_dir(run_id)))
        return parse_mailbox(out)

    def add_request(
        self, run_id: str, request_id: str, prompts_gz: bytes, callback_url: str | None = None
    ) -> dict:
        """Puts a request's prompts (llm_batch's format, gzipped JSON lines) in a running
        llm_serve job's inbox. Its callback address, if any, goes in first, so the job
        finds it when it takes the request. A request name is used once per run."""
        check_request_id(request_id)
        run_dir = self.run_dir(run_id)
        box = self.mailbox(run_id)
        if not box["slurm_job_id"]:
            raise BlytheError(
                f"{run_id} wasn't started by start_server, so nothing reads its inbox."
            )
        state = (box["status"] or {}).get("state")
        if state in ("stopped", "failed") or box["stop_asked"]:
            raise BlytheError(
                f"The server in {run_id} has stopped or been asked to stop ({state}):"
                " start a new one."
            )
        used = box["waiting"] + box["answered"] + box["refused"] + box["part_answered"]
        if request_id in used:
            raise BlytheError(f"Request {request_id} was already sent to {run_id}.")
        inbox = posixpath.join(run_dir, "inbox")
        self.must(f"mkdir -p {q(inbox)}", "make the inbox")
        if callback_url:
            self.write_new(posixpath.join(inbox, f"{request_id}.callback"), callback_url, True)
        self.write_new(posixpath.join(inbox, f"{request_id}.jsonl.gz"), prompts_gz)
        print(f"Added request {request_id} to {run_dir}/inbox", flush=True)
        return {
            "run_dir": run_dir,
            "request_id": request_id,
            "bytes": len(prompts_gz),
            "prompts_sha256": hashlib.sha256(prompts_gz).hexdigest(),
        }

    def collect(self, run_id: str, request_id: str) -> dict:
        """A request's answers if it is done: {"state": "answered", "summary", "results"
        (the gzipped results file's bytes)}; {"state": "refused", "error"}; or, while it
        waits, {"state": "waiting"}."""
        check_request_id(request_id)
        outbox = posixpath.join(self.run_dir(run_id), "outbox")
        summary_path = posixpath.join(outbox, f"{request_id}.json")
        if self.exists(summary_path):
            return {
                "state": "answered",
                "summary": json.loads(self.read(summary_path)),
                "results": self.read(posixpath.join(outbox, f"{request_id}.jsonl.gz")),
            }
        error_path = posixpath.join(outbox, f"{request_id}.error.json")
        if self.exists(error_path):
            return {"state": "refused", "error": json.loads(self.read(error_path))}
        return {"state": "waiting"}

    def events(self, run_id: str, offset: int = 0) -> dict:
        """The requests an llm_serve job has finished since `offset`, and its status."""
        _, out, _ = self._exec(events_command(self.run_dir(run_id), offset))
        return parse_events(out, offset)

    def stop_server(self, run_id: str) -> None:
        """Asks the llm_serve job to stop once its inbox is empty."""
        run_dir = self.run_dir(run_id)
        if not self.exists(run_dir):
            raise BlytheError(f"{run_id} isn't on Blythe.")
        self.write(posixpath.join(run_dir, "stop"), "")
