"""Running fixed SLURM jobs on Blythe, the university's GPU cluster, from Windmill.

Windmill logs in to Blythe over SSH as Rowan (csujxb), with his key and a 2FA code made from
his 2FA secret. SCRTP allowed this on 19 Nov 2026 until they offer a web interface. It is
his own university account, so only he may use it: the three secrets are Windmill variables
under u/rshields2004/, which only his runs (and workspace admins) can read, and check_owner()
stops any run that isn't his before it reads them, admins' runs included.

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

Blythe's login node closes SFTP sessions, so files go over the SSH command channel
(cat / head / find). Every command runs on its own channel of one login, so one task logs
in, and uses one 2FA code, only once. A code works only once, so a refused code is retried
once with the next one.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import posixpath
import re
import shlex
import socket
import time
from urllib.parse import parse_qs, urlparse

HOST = "blythe.scrtp.warwick.ac.uk"
USERNAME = "csujxb"
OWNER = "rshields2004"  # the Windmill user whose Blythe account it is: no one else may use it
BASE = "narratrace"  # in the home folder on Blythe

# Blythe's host keys, from Rowan's known_hosts on the project server (19 Nov 2026). A login
# stops before sending anything if Blythe shows another key.
KNOWN_HOST_KEYS = {
    "SHA256:4H8j81Qj1P4A1k33JcUGlr4pT82hSradiUt8khVX/j4",  # RSA
    "SHA256:TPNsZGtGuRZsWBZDjjdwyJBb1zEXPfokFNI6MsFckls",  # ECDSA
    "SHA256:e8tZHZ5vktblxUSX+pxLYLPlmX1Pa6qNuXMAxb3BqAo",  # ED25519
}
KEY_VARIABLE = "u/rshields2004/blythe_ssh_key"
PASSPHRASE_VARIABLE = "u/rshields2004/blythe_key_passphrase"
TOTP_VARIABLE = "u/rshields2004/blythe_totp_secret"

# The job files Windmill may submit: ~/narratrace/jobs/<name>.sbatch on Blythe.
JOBS = {"test_gpu", "llm_batch"}
MAX_HOURS = 48  # the gpu partition's limit

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
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


def check_owner() -> None:
    """Stops the run unless Rowan started it (Windmill names the user in WM_USERNAME)."""
    user = os.environ.get("WM_USERNAME")
    if user != OWNER:
        raise BlytheError(
            f"Only {OWNER} can run this: it uses his own Blythe account. This run belongs to"
            f" {user or 'no Windmill user'}."
        )


def check_job(job: str) -> None:
    if job not in JOBS:
        raise BlytheError(f"{job!r} is not one of the job files Windmill may run: {sorted(JOBS)}")


def check_name(name: str) -> None:
    """Run folder and file names: letters, digits, '.', '_' and '-', so they're safe in paths
    and commands."""
    if not SAFE_NAME.match(name):
        raise BlytheError(f"Unsafe name {name!r}: use letters, digits, '.', '_' and '-'.")


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


def sbatch_command(job_file: str, run_dir: str, hours: int | None = None) -> str:
    limit = time_limit(hours)
    options = f" --time={limit}" if limit else ""
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


def load_secrets():
    """Rowan's key and 2FA generator, from his Windmill variables (only in his own runs)."""
    import pyotp
    import wmill

    check_owner()
    key = _load_key(
        _clean_key_text(wmill.get_variable(KEY_VARIABLE) or ""),
        wmill.get_variable(PASSPHRASE_VARIABLE) or "",
    )
    totp = pyotp.TOTP(_clean_totp_secret(wmill.get_variable(TOTP_VARIABLE) or ""))
    totp.now()  # fails here if the secret isn't valid
    return key, totp


# ---------------------------------------------------------------- the login


class TwoFactorRefused(BlytheError):
    pass


def _as_bytes(data) -> bytes:
    return data if isinstance(data, bytes) else data.encode()


class Blythe:
    """One SSH login to Blythe. Use as `with Blythe() as b:`."""

    def __enter__(self) -> Blythe:
        key, totp = load_secrets()
        try:
            self.transport = self._login(key, totp)
        except TwoFactorRefused:
            wait = 31 - int(time.time()) % 30
            print(f"2FA code refused; trying once more with the next code in {wait} s", flush=True)
            time.sleep(wait)
            self.transport = self._login(key, totp)
        status, out, err = self.sh('echo "$HOME"')
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if status != 0 or not lines or not lines[-1].startswith("/"):
            self.transport.close()
            raise BlytheError(f"Couldn't read the home folder on Blythe: {(err or out).strip()}")
        self.home = lines[-1]
        return self

    def __exit__(self, *exc) -> None:
        self.transport.close()

    @staticmethod
    def _login(key, totp):
        import paramiko

        sock = socket.create_connection((HOST, 22), timeout=15)
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=20)
            seen = fingerprint(transport.get_remote_server_key())
            if seen not in KNOWN_HOST_KEYS:
                raise BlytheError(f"Blythe's host key {seen} isn't a known one. Nothing was sent.")
            remaining = transport.auth_publickey(USERNAME, key)
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
                    transport.auth_interactive(USERNAME, answer)
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
        callback_url: str,
        hours: int | None = None,
    ) -> dict:
        """Makes the run folder, uploads files into its input/, leaves the wake-up address,
        and submits the job. The run folder must be new, so runs never mix."""
        check_job(job)
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
        status, out, err = self.slurm(sbatch_command(job_file, run_dir, hours))
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
