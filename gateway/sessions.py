"""One session per person: their own Blythe login, their own llm_serve job, and their calls.

A person's calls all go through one SSH login to Blythe (one 2FA code), held open with
keep-alives and made again if it drops. Calls arriving within batch_seconds of each other
go to the job as one request, so the GPU answers them together. While answers are awaited,
the session reads the job's events.log (one small command each check_seconds) and collects
each finished request.

The job is started when the first call arrives and stops by itself after idle_minutes with
nothing to do. If it has stopped or failed while calls were waiting (the time limit, a
cancelled job), the calls are sent again to a new job. A job that fails to start twice in a
row fails the calls waiting for it, with the job's own error.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import gzip
import json
import secrets
import threading
import time
import traceback
from datetime import datetime, timezone

from .openai_format import RequestError


class GatewayError(RuntimeError):
    """Blythe or the job failed; becomes an HTTP 502."""


@dataclasses.dataclass
class Settings:
    model: str = "Qwen/Qwen3.8-27B-FP8"
    max_model_len: int = 32768
    idle_minutes: int = 30  # the job stops after this long with nothing to do
    hours: int = 8  # the job's SLURM time limit
    job_poll_seconds: float = 0.5  # how often the job looks in its inbox
    batch_seconds: float = 0.1  # calls this close together are sent as one request
    max_batch: int = 256
    check_seconds: float = 0.5  # how often the session reads events.log while waiting
    slurm_check_seconds: float = 120  # how often to ask SLURM whether the job is still there
    login_idle_seconds: float = 1800  # close the Blythe login after this long unused
    timeout_seconds: float = 3600  # a call waits this long at most (a cold start queues)
    max_failed_starts: int = 2
    max_errors: int = 3  # Blythe errors in a row before waiting calls are failed

    def server_request(self) -> dict:
        """input/request.json for the person's llm_serve job."""
        return {
            "model": self.model,
            "max_model_len": self.max_model_len,
            "top_logprobs": 20,
            "chunk_size": 64,
            "gpu_memory_utilization": 0.9,
            "max_num_seqs": 128,
            "seed": 0,
            "idle_minutes": self.idle_minutes,
            "poll_seconds": self.job_poll_seconds,
            "purpose": "gateway",
        }


@dataclasses.dataclass(eq=False)  # each call is itself, even with the same prompt
class Call:
    prompt: dict
    future: concurrent.futures.Future


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def log(user: str, message: str) -> None:
    print(f"{datetime.now(timezone.utc):%H:%M:%S} [{user}] {message}", flush=True)


class Session:
    """connect() returns a connected Blythe login for this person (blythe.Blythe)."""

    def __init__(self, user: str, connect, settings: Settings, clock=time.monotonic):
        self.user, self.connect, self.settings, self.clock = user, connect, settings, clock
        self.cond = threading.Condition()
        self.queue: list[Call] = []
        self.sent: dict[str, list[Call]] = {}
        self.blythe = None
        self.run_id: str | None = None
        self.slurm_job_id: str | None = None
        self.offset = 0
        self.state: str | None = None
        self.last_used = clock()
        self.last_slurm_check = clock()
        self.failed_starts = 0
        self.errors = 0
        self.thread: threading.Thread | None = None
        self.stopped = False

    # --- what the web app calls

    def submit(self, prompt: dict) -> concurrent.futures.Future:
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self.cond:
            self.queue.append(Call(prompt, future))
            self.last_used = self.clock()
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True)
                self.thread.start()
            self.cond.notify()
        return future

    def status(self) -> dict:
        with self.cond:
            return {
                "user": self.user,
                "run_id": self.run_id,
                "slurm_job_id": self.slurm_job_id,
                "job_state": self.state,
                "calls_waiting_to_send": len(self.queue),
                "calls_with_the_job": sum(len(calls) for calls in self.sent.values()),
                "logged_in": bool(self.blythe is not None and self.blythe.alive()),
            }

    def close(self) -> None:
        with self.cond:
            self.stopped = True
            self.cond.notify()
        if self.blythe is not None:
            self.blythe.close()

    # --- the session's own thread: all Blythe work happens here, one thing at a time

    def _loop(self) -> None:
        while True:
            with self.cond:
                while not self.queue and not self.sent and not self.stopped:
                    if not self.cond.wait(timeout=60):
                        self._close_if_idle()
                if self.stopped:
                    return
                if self.queue and not self.sent:
                    # gather calls arriving together into one request: wait out the whole
                    # window (each new call wakes this wait, so keep waiting until it ends)
                    deadline = time.monotonic() + self.settings.batch_seconds
                    while len(self.queue) < self.settings.max_batch and not self.stopped:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.cond.wait(timeout=remaining)
                batch = [c for c in self.queue if not c.future.cancelled()]
                batch = batch[: self.settings.max_batch]
                self.queue = [c for c in self.queue if c not in batch and not c.future.cancelled()]
            try:
                if batch:
                    self._send(batch)
                if self.sent:
                    self._check()
                self.errors = 0
            except Exception as error:  # noqa: BLE001 - kept going: the next round retries
                self._trouble(error, batch)
            if self.sent and not self.queue:
                time.sleep(self.settings.check_seconds)

    def _trouble(self, error: Exception, batch: list[Call]) -> None:
        self.errors += 1
        log(self.user, f"Blythe error {self.errors}: {error}\n{traceback.format_exc(limit=3)}")
        if self.blythe is not None:
            self.blythe.close()
        self.blythe = None
        unsent = [c for c in batch if not any(c in calls for calls in self.sent.values())]
        with self.cond:
            self.queue[:0] = unsent  # try them again next round
        if self.errors >= self.settings.max_errors:
            self._fail_everything(GatewayError(f"Blythe kept failing: {error}"))
            self.errors = 0
        else:
            time.sleep(min(30, 2**self.errors))

    def _fail_everything(self, error: Exception) -> None:
        with self.cond:
            calls = self.queue + [c for calls in self.sent.values() for c in calls]
            self.queue, self.sent = [], {}
        for call in calls:
            if not call.future.done():
                call.future.set_exception(error)

    def _close_if_idle(self) -> None:
        if (
            self.blythe is not None
            and self.clock() - self.last_used > self.settings.login_idle_seconds
        ):
            log(self.user, "Closing the Blythe login: unused for a while")
            self.blythe.close()
            self.blythe = None

    def _login(self):
        if self.blythe is None or not self.blythe.alive():
            log(self.user, "Logging in to Blythe")
            self.blythe = self.connect()
        return self.blythe

    def _start_server(self, b) -> None:
        run_id = f"gw-{stamp()}-{secrets.token_hex(2)}"
        submitted = b.start_server(run_id, self.settings.server_request(), self.settings.hours)
        self.run_id, self.slurm_job_id = run_id, submitted["slurm_job_id"]
        self.offset, self.state = 0, "submitted"
        self.last_slurm_check = self.clock()
        log(self.user, f"Started llm_serve as SLURM job {self.slurm_job_id} in {run_id}")

    def _send(self, batch: list[Call]) -> None:
        b = self._login()
        if self.run_id is None:
            self._start_server(b)
        request_id = f"c{stamp()}-{secrets.token_hex(3)}"
        prompts = [{**call.prompt, "id": f"p{n}"} for n, call in enumerate(batch)]
        data = "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in prompts)
        try:
            b.add_request(self.run_id, request_id, gzip.compress(data.encode(), mtime=0))
        except Exception as error:
            if "stopped or been asked to stop" not in str(error):
                raise
            log(self.user, f"{self.run_id} has stopped; starting a new job")
            self._start_server(b)
            b.add_request(self.run_id, request_id, gzip.compress(data.encode(), mtime=0))
        with self.cond:
            self.sent[request_id] = batch
        log(self.user, f"Sent {len(batch)} call(s) as {request_id}")

    def _check(self) -> None:
        b = self._login()
        seen = b.events(self.run_id, self.offset)
        self.offset = seen["offset"]
        status = seen["status"] or {}
        self.state = status.get("state") or self.state
        for event in seen["events"]:
            with self.cond:
                calls = self.sent.pop(event.get("request"), None)
            if calls:
                self._deliver(b, event["request"], calls)
        if self.state == "waiting" or self.state == "answering":
            self.failed_starts = 0
        dead = self.state in ("stopped", "failed")
        if not dead and self.clock() - self.last_slurm_check > self.settings.slurm_check_seconds:
            self.last_slurm_check = self.clock()
            dead = bool(self.slurm_job_id) and b.job_state(self.slurm_job_id)["finished"]
        if dead and self.sent:
            self._job_ended(status)

    def _deliver(self, b, request_id: str, calls: list[Call]) -> None:
        got = b.collect(self.run_id, request_id)
        if got["state"] == "refused":
            error = RequestError((got.get("error") or {}).get("error") or "refused by the job")
            for call in calls:
                if not call.future.done():  # a call that timed out was cancelled
                    call.future.set_exception(error)
            return
        if got["state"] != "answered":
            raise GatewayError(f"{request_id} was reported done but isn't answered")
        lines = gzip.decompress(got["results"]).decode().splitlines()
        by_id = {r["id"]: r for r in map(json.loads, filter(None, lines))}
        for n, call in enumerate(calls):
            result = by_id.get(f"p{n}")
            if call.future.done():
                continue
            if result is None:
                call.future.set_exception(GatewayError("the job gave no answer for this call"))
            else:
                call.future.set_result({**result, "run_id": self.run_id})

    def _job_ended(self, status: dict) -> None:
        """The job stopped or failed with calls still waiting: send them to a new one."""
        failed = self.state == "failed"
        self.failed_starts = self.failed_starts + 1 if failed else 0
        reason = status.get("error") or status.get("reason") or "it ended"
        log(self.user, f"{self.run_id} ended ({reason}) with calls waiting")
        with self.cond:
            waiting = [c for calls in self.sent.values() for c in calls]
            self.sent = {}
            self.run_id, self.slurm_job_id, self.state = None, None, None
        if self.failed_starts >= self.settings.max_failed_starts:
            self.failed_starts = 0
            for call in waiting:
                if not call.future.done():
                    call.future.set_exception(GatewayError(f"the model job failed: {reason}"))
            return
        with self.cond:
            self.queue[:0] = [c for c in waiting if not c.future.done()]
