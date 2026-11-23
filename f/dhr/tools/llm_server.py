# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "paramiko==5.1.0",
#     "pyotp==2.12.1",
#     "wmill==1.875.0",
# ]
# ///
"""Blythe LLM server: keep a model loaded on a Blythe GPU and send it prompts while it runs.

Starts narratrace-slurm's llm_serve job, which loads the model once and answers prompts as
they are added, so more work doesn't wait in the SLURM queue or load the model again. It logs
in with Rowan's own Blythe account (f/dhr/blythe.py), so only Rowan can run it.

action:
    start        submits the job in a new run folder and returns its run_id. Prompts given
                 with it become the first request.
    add prompts  adds prompts to that job's inbox as a new request (request_id, made up if
                 left empty). Requests are answered whole, one at a time, oldest first.
    status       the SLURM job, what the job is doing, and which requests are waiting,
                 answered or refused.
    collect      a request's answers, once it is done.
    stop         asks the job to stop once everything already waiting is answered.
The job also stops by itself after idle_minutes with nothing to do (so it doesn't hold a
shared GPU), and 15 minutes before its time limit (hours), leaving anything unanswered in
its inbox.

prompts is a list. Each prompt is llm_batch's own format,
    {"id": "...", "messages": [{"role": "system" | "user", "content": "..."}], "labels": [...]}
or, for short, {"question": "...", "labels": ["A", "B"], "system": "..." (optional)}. The
model answers each with one of its labels, and the probability of every label it considered
is kept. Ids are made up (p1, p2, ...) if left out.

Each action is one login to Blythe, and each login needs a new 2FA code, so a login at the
same moment as another (stage 5 checking its jobs, say) waits half a minute for the next
code. dry_run=True checks the inputs and shows what would be sent, without contacting Blythe.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import secrets
from datetime import datetime, timezone
from typing import Literal

from ..blythe import MAX_HOURS, Blythe, BlytheError, check_name, check_owner, check_request_id
from ..version import GIT_COMMIT

MODEL = "Qwen/Qwen3.8-27B-FP8"  # downloaded on Blythe by setup_env.sbatch
# llm_batch.py's settings (narratrace-slurm explains each), as stage 5 sends them.
SETTINGS = {
    "top_logprobs": 20,
    "chunk_size": 512,
    "gpu_memory_utilization": 0.9,
    "max_num_seqs": 128,
    "seed": 0,
    "poll_seconds": 5,
}
ROLES = {"system", "user", "assistant"}
MAX_LABELS, MAX_LABEL_CHARS = 100, 20  # the same limits llm_serve.py checks
IDLE_MINUTES = (1, 240)
MAX_MODEL_LEN = (1024, 65536)


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def new_run_id() -> str:
    return f"serve-{stamp()}-{secrets.token_hex(2)}"


def new_request_id() -> str:
    return f"r{stamp()}-{secrets.token_hex(2)}"


# ---------------------------------------------------------------- prompts


def check_prompt(prompt: dict, n: int) -> None:
    """What llm_serve.py would refuse, refused here first, with the prompt's number."""
    where = f"prompt {n} ({prompt['id']!r})"
    messages = prompt["messages"]
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"{where}: messages must be a list of chat messages")
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ROLES
            or not isinstance(message["content"], str)
        ):
            raise ValueError(f"{where}: each message is {{role, content}}, role one of {ROLES}")
    labels = prompt["labels"]
    if (
        not isinstance(labels, list)
        or not 1 <= len(labels) <= MAX_LABELS
        or len(set(labels)) != len(labels)
        or not all(isinstance(x, str) and 0 < len(x) <= MAX_LABEL_CHARS for x in labels)
    ):
        raise ValueError(
            f"{where}: labels must be 1 to {MAX_LABELS} different short strings"
            f" (up to {MAX_LABEL_CHARS} characters)"
        )


def normalise(prompts) -> list[dict]:
    """The prompts in llm_batch's format, from either form."""
    if not isinstance(prompts, list) or not prompts:
        raise ValueError("prompts must be a list with at least one prompt")
    out, seen = [], set()
    for n, given in enumerate(prompts, 1):
        if not isinstance(given, dict):
            raise ValueError(f"prompt {n} must be an object")
        if "question" in given:
            allowed = {"id", "question", "labels", "system", "meta"}
            if not isinstance(given["question"], str) or not given["question"].strip():
                raise ValueError(f"prompt {n}: question must be text")
            system = given.get("system")
            messages = [{"role": "system", "content": system}] if system else []
            messages.append({"role": "user", "content": given["question"]})
        else:
            allowed = {"id", "messages", "labels", "meta"}
            messages = given.get("messages")
        unknown = sorted(set(given) - allowed)
        if unknown:
            raise ValueError(f"prompt {n}: unknown fields {unknown}")
        prompt = {
            "id": str(given.get("id") or f"p{n}"),
            "messages": messages,
            "labels": given.get("labels"),
            "meta": given.get("meta"),
        }
        check_prompt(prompt, n)
        if prompt["id"] in seen:
            raise ValueError(f"prompt id {prompt['id']!r} appears twice")
        seen.add(prompt["id"])
        out.append(prompt)
    return out


def gzip_prompts(prompts: list[dict]) -> bytes:
    lines = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in prompts)
    return gzip.compress(lines.encode(), mtime=0)


def read_answers(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in gzip.decompress(raw).decode().splitlines() if line]


def server_request(max_model_len: int, idle_minutes: int) -> dict:
    """input/request.json for llm_serve: the model and its settings, fixed for the job."""
    return {
        "model": MODEL,
        "max_model_len": max_model_len,
        **SETTINGS,
        "idle_minutes": idle_minutes,
        "purpose": "f/dhr/tools/llm_server",
        "git_commit": GIT_COMMIT,
    }


def check_settings(hours: int, idle_minutes: int, max_model_len: int) -> None:
    for name, value, (low, high) in (
        ("hours", hours, (1, MAX_HOURS)),
        ("idle_minutes", idle_minutes, IDLE_MINUTES),
        ("max_model_len", max_model_len, MAX_MODEL_LEN),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise ValueError(f"{name} must be a whole number from {low} to {high}")


# ---------------------------------------------------------------- the actions


def start(run_id, prompts, request_id, hours, idle_minutes, max_model_len, dry_run) -> dict:
    check_settings(hours, idle_minutes, max_model_len)
    run_id = run_id or new_run_id()
    check_name(run_id)
    request = server_request(max_model_len, idle_minutes)
    first = normalise(prompts) if prompts else None
    request_id = (request_id or new_request_id()) if first else None
    if request_id:
        check_request_id(request_id)
    if dry_run:
        return {
            "result": "Dry run: nothing was sent to Blythe.",
            "run_id": run_id,
            "request.json": request,
            "first_request": request_id,
            "n_prompts": len(first or []),
        }
    with Blythe() as b:
        submitted = b.start_server(run_id, request, hours)
        added = b.add_request(run_id, request_id, gzip_prompts(first)) if first else None
    return {
        "result": (
            f"Started the server as SLURM job {submitted['slurm_job_id']}. It loads the model"
            " once it gets a GPU; use action 'status' to see when it is waiting for prompts."
        ),
        "run_id": run_id,
        "slurm_job_id": submitted["slurm_job_id"],
        "first_request": added and {**added, "n_prompts": len(first)},
        "stops_after": f"{idle_minutes} min with nothing to do, or {hours} h at most",
    }


def add(run_id, prompts, request_id, dry_run) -> dict:
    prompts = normalise(prompts)
    request_id = request_id or new_request_id()
    check_request_id(request_id)
    data = gzip_prompts(prompts)
    if dry_run:
        return {
            "result": "Dry run: nothing was sent to Blythe.",
            "run_id": run_id,
            "request_id": request_id,
            "n_prompts": len(prompts),
            "prompts_sha256": hashlib.sha256(data).hexdigest(),
            "first_prompt": prompts[0],
        }
    with Blythe() as b:
        added = b.add_request(run_id, request_id, data)
    return {
        "result": f"Added {len(prompts)} prompt(s) as request {request_id}.",
        "run_id": run_id,
        **added,
        "n_prompts": len(prompts),
    }


def status(run_id) -> dict:
    with Blythe() as b:
        box = b.mailbox(run_id)
        job = b.job_state(box["slurm_job_id"]) if box["slurm_job_id"] else None
    return {"run_id": run_id, "slurm_job": job, **box}


def collect(run_id, request_id) -> dict:
    if not request_id:
        raise ValueError("Give the request_id to collect ('add prompts' returned it).")
    check_request_id(request_id)
    with Blythe() as b:
        got = b.collect(run_id, request_id)
    if got["state"] != "answered":
        return {"run_id": run_id, "request_id": request_id, **got}
    answers = read_answers(got["results"])
    if len(answers) != got["summary"].get("n_prompts"):
        raise BlytheError(
            f"Request {request_id} is marked done with {got['summary'].get('n_prompts')}"
            f" prompts, but {len(answers)} answers came back."
        )
    return {
        "run_id": run_id,
        "request_id": request_id,
        "state": "answered",
        "summary": got["summary"],
        "answers": [
            {k: a.get(k) for k in ("id", "answer", "probabilities", "error", "meta")}
            for a in answers
        ],
    }


def stop(run_id) -> dict:
    with Blythe() as b:
        b.stop_server(run_id)
    return {
        "run_id": run_id,
        "result": "Asked the server to stop once everything already waiting is answered.",
    }


def main(
    action: Literal["start", "add prompts", "status", "collect", "stop"] = "status",
    run_id: str = "",
    prompts: list[dict] | None = None,
    request_id: str = "",
    hours: int = 4,
    idle_minutes: int = 20,
    max_model_len: int = 8192,
    dry_run: bool = False,
):
    """action: what to do (see above). run_id: the server's run folder, as 'start' returned
    it (made up by 'start' if left empty). prompts: for 'start' and 'add prompts'.
    request_id: the request to collect, or a name for the one being added (made up if left
    empty). hours, idle_minutes, max_model_len: for 'start' only: its SLURM time limit, how
    long it waits with nothing to do before stopping, and the longest prompt in tokens.
    dry_run: only check the inputs and show what would be sent."""
    check_owner()  # Rowan's Blythe account: no one else may run this, not even a dry run
    run_id, request_id = (run_id or "").strip(), (request_id or "").strip()
    if action == "start":
        return start(run_id, prompts, request_id, hours, idle_minutes, max_model_len, dry_run)
    if not run_id:
        raise ValueError("Give the run_id that 'start' returned.")
    check_name(run_id)
    if action == "add prompts":
        return add(run_id, prompts, request_id, dry_run)
    if dry_run:
        return {"result": f"Dry run: '{action}' would log in to Blythe; nothing was done."}
    if action == "status":
        return status(run_id)
    if action == "collect":
        return collect(run_id, request_id)
    if action == "stop":
        return stop(run_id)
    raise ValueError(f"Unknown action {action!r}")
