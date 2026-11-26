"""The LLM gateway: an OpenAI-style API to the model on Blythe, for the team's code.

Run with `uvicorn --factory gateway.app:create_app` (gateway/Dockerfile does).

    POST /v1/chat/completions   OpenAI's chat completions (see openai_format.py for what
                                is supported, and the extra "labels" and "thinking")
    GET  /v1/models             the model it serves
    GET  /status                your session: your job on Blythe and your waiting calls
    GET  /usage                 your calls and tokens so far
    GET  /health                for Docker's health check (no key needed)

The API key is your Windmill token: WM_TOKEN inside a Windmill script, or a token you make
in Windmill (Account settings > Tokens) for your own laptop. The gateway asks Windmill who
the token belongs to, reads that person's own Blythe variables with the same token
(u/<them>/blythe_*, as f/dhr/blythe.py does), and runs their calls on their own Blythe
account, in their own llm_serve job. It only ever acts as the person whose token it holds.

It is only reachable inside the project server: from Windmill scripts as
http://llm-gateway:8000/v1, and from a laptop through an SSH tunnel to 127.0.0.1:8100
(gateway/README.md).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from f.dhr.blythe import Blythe, BlytheError, load_account

from .openai_format import RequestError, to_completion, to_prompt
from .sessions import MODELS, GatewayError, Session, Settings, describe


def error(status: int, message: str, kind: str, code: str | None = None) -> JSONResponse:
    """An error in OpenAI's shape, so the openai client raises the matching exception."""
    return JSONResponse(
        {"error": {"message": message, "type": kind, "code": code, "param": None}}, status
    )


class Windmill:
    """Asks Windmill who a token belongs to, and reads their variables with it."""

    def __init__(self, url: str, workspace: str, transport=None):
        self.workspace = workspace
        self.http = httpx.Client(base_url=url, timeout=20, transport=transport)
        self.known: dict[str, tuple[str, float]] = {}  # token's hash -> (user, when checked)

    def user(self, token: str) -> str | None:
        key = hashlib.sha256(token.encode()).hexdigest()
        cached = self.known.get(key)
        if cached and time.monotonic() - cached[1] < 60:
            return cached[0]
        response = self.http.get(
            f"/api/w/{self.workspace}/users/whoami", headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code in (401, 403):
            return None
        response.raise_for_status()
        user = response.json().get("username")
        if user:
            self.known[key] = (user, time.monotonic())
        return user

    def variables(self, token: str):
        def get_variable(path: str):
            response = self.http.get(
                f"/api/w/{self.workspace}/variables/get_value/{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return response.json()

        return get_variable


class Gateway:
    def __init__(self, windmill: Windmill, settings: Settings, data: Path, connect=None):
        self.windmill, self.settings, self.data = windmill, settings, data
        self.sessions: dict[tuple[str, str], Session] = {}  # (user, model key) -> session
        self.lock = threading.Lock()
        # One login at a time per person: their sessions (one per model) share a 2FA secret,
        # and Blythe refuses a code used moments before.
        self.login_locks: dict[str, threading.Lock] = {}
        # connect(user, token) -> a connected Blythe login; replaced in the tests
        self.connect = connect or self._connect

    def _connect(self, user: str, token: str):
        account = load_account(user, self.windmill.variables(token))
        return Blythe(account, keepalive=30).connect()

    def _login(self, user: str, session: Session):
        with self.lock:
            lock = self.login_locks.setdefault(user, threading.Lock())
        with lock:
            return self.connect(user, session.token)

    def session(self, user: str, token: str, model: str | None = None) -> Session:
        """The person's session for one model (a key in MODELS; the default if None)."""
        model = model or self.settings.default_model
        with self.lock:
            session = self.sessions.get((user, model))
            if session is None:
                session = Session(
                    user, lambda: self._login(user, session), self.settings, model=model
                )
                self.sessions[(user, model)] = session
            session.token = token  # the newest, for logging in again later
            return session

    def record(self, entry: dict) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        with open(self.data / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def usage(self, user: str) -> dict:
        totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        path = self.data / "usage.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                if entry.get("user") == user:
                    totals["calls"] += 1
                    totals["prompt_tokens"] += entry.get("prompt_tokens", 0)
                    totals["completion_tokens"] += entry.get("completion_tokens", 0)
        return {"user": user, **totals}


def settings_from_env() -> Settings:
    given = {}
    for field in dataclasses.fields(Settings):
        value = os.environ.get(f"GATEWAY_{field.name.upper()}")
        if value is not None:  # e.g. GATEWAY_IDLE_MINUTES=45, GATEWAY_DEFAULT_MODEL=qwen-27b-fp8
            given[field.name] = type(field.default)(value)
    return Settings(**given)


def create_app(gateway: Gateway | None = None) -> FastAPI:
    app = FastAPI(title="NarraTrace LLM gateway", docs_url=None, redoc_url=None)
    if gateway is None:
        gateway = Gateway(
            Windmill(
                os.environ.get("WINDMILL_URL", "http://windmill-server:8000"),
                os.environ.get("WINDMILL_WORKSPACE", "dhr"),
            ),
            settings_from_env(),
            Path(os.environ.get("GATEWAY_DATA", "/data")),
        )
    app.state.gateway = gateway

    def caller(request: Request):
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            return None, None, error(401, "Send your Windmill token as the API key.", "auth")
        try:
            user = gateway.windmill.user(token)
        except httpx.HTTPError as problem:
            return None, None, error(502, f"Couldn't ask Windmill: {problem}", "server")
        if not user:
            return None, None, error(401, "Windmill didn't accept that token.", "auth")
        return user, token, None

    def unknown_model(name):
        return error(
            404,
            f'Model {name!r} isn\'t served here; use one of {sorted(MODELS)} (or "default",'
            f" {gateway.settings.default_model}).",
            "invalid_request_error",
            "model_not_found",
        )

    @app.get("/health")
    def health():
        default = gateway.settings.default_model
        return {"ok": True, "default": default, "model": MODELS[default]["model"]}

    @app.get("/v1/models")
    def models(request: Request):
        user, _, problem = caller(request)
        if problem:
            return problem
        return {
            "object": "list",
            "default": gateway.settings.default_model,
            "data": [describe(key) for key in MODELS],
        }

    @app.get("/status")
    async def status(request: Request, model: str = "default", refresh: bool = False):
        """Your job for one model as the gateway last saw it; refresh=true asks Blythe first."""
        user, token, problem = caller(request)
        if problem:
            return problem
        key = gateway.settings.resolve(model)
        if key is None:
            return unknown_model(model)
        if refresh:
            return await refreshed(user, token, key, start=False)
        session = gateway.sessions.get((user, key))
        return session.status() if session else {"user": user, "model": key, "run_id": None}

    @app.post("/start")
    async def start(request: Request, model: str = "default"):
        """Submits your job for one model if it isn't there, and returns at once (it may then
        wait in SLURM's queue for hours or days: poll /status?refresh=true&model=...)."""
        user, token, problem = caller(request)
        if problem:
            return problem
        key = gateway.settings.resolve(model)
        if key is None:
            return unknown_model(model)
        return await refreshed(user, token, key, start=True)

    async def refreshed(user: str, token: str, key: str, start: bool):
        session = gateway.session(user, token, key)
        try:
            return await asyncio.to_thread(session.refresh, start)
        except (GatewayError, concurrent.futures.TimeoutError) as problem:
            return JSONResponse({"error": {"message": f"{problem}"}}, status_code=502)

    @app.get("/usage")
    def usage(request: Request):
        user, _, problem = caller(request)
        if problem:
            return problem
        return gateway.usage(user)

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        user, token, problem = caller(request)
        if problem:
            return problem
        try:
            body = await request.json()
        except ValueError:
            return error(400, "The body must be JSON.", "invalid_request_error")
        key = gateway.settings.resolve(body.get("model"))
        if key is None:
            return unknown_model(body.get("model"))
        try:
            prompt = to_prompt(body, "p")
        except RequestError as problem:
            return error(400, str(problem), "invalid_request_error", problem.code)

        started = time.monotonic()
        future = gateway.session(user, token, key).submit(prompt)
        try:
            result = await asyncio.wait_for(
                asyncio.wrap_future(future), timeout=gateway.settings.timeout_seconds
            )
        except asyncio.TimeoutError:
            future.cancel()
            return error(
                504, "No answer in time: the model may still be waiting for a GPU.", "timeout"
            )
        except RequestError as problem:
            return error(400, str(problem), "invalid_request_error")
        except (GatewayError, BlytheError) as problem:
            return error(502, str(problem), "server")
        if result.get("error"):
            if result["error"].startswith("prompt too long"):
                return error(
                    400, result["error"], "invalid_request_error", "context_length_exceeded"
                )
            return error(502, result["error"], "server")
        completion = to_completion(result, key, bool(prompt.get("tools")))
        gateway.record(
            {
                "at": int(time.time()),
                "user": user,
                "model": key,
                "kind": "labels" if "labels" in prompt else "chat",
                "run_id": result.get("run_id"),
                "seconds": round(time.monotonic() - started, 2),
                "prompt_tokens": completion["usage"]["prompt_tokens"],
                "completion_tokens": completion["usage"]["completion_tokens"],
            }
        )
        return completion

    return app
