"""Tests for the LLM gateway (gateway/): an OpenAI-style API that runs each person's calls on
their own Blythe account, in their own llm_serve job.

Blythe and the job are an in-memory stand-in here (FakeBlythe), Windmill is a stand-in that
knows two tokens, and the job's answers are made up from the prompts. The real job's side has
its own tests in narratrace-slurm.
"""

import gzip
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from f.dhr.blythe import BlytheError
from gateway.app import Gateway, Windmill, create_app, settings_from_env
from gateway.openai_format import RequestError, parse_tool_calls, to_completion, to_prompt
from gateway.sessions import PROFILES, Settings

TOKENS = {"tok-rowan": "rshields2004", "tok-ks": "ks0610"}
MODEL = Settings().model


class Account:
    """One person's Blythe account, with the llm_serve jobs started in it."""

    def __init__(self):
        self.runs, self.logins, self.next_job = {}, [], 5000
        self.resources = {}  # run id -> the gpus and mem_gb start_server asked for
        self.paused = False  # jobs don't answer while paused
        self.fail_every_start = None  # an error every new job fails with

    def answer(self, prompt):
        said = prompt["messages"][-1].get("content") or ""
        result = {"id": prompt["id"], "meta": prompt.get("meta"), "error": None}
        if "labels" in prompt:
            return {
                **result,
                "answer": prompt["labels"][0],
                "probabilities": {prompt["labels"][0]: 0.9},
                "prompt_tokens": 5,
            }
        result.update(prompt_tokens=7, completion_tokens=3, reasoning=None, finish_reason="stop")
        if "TOO LONG" in said:
            return {**result, "text": None, "error": "prompt too long (99999 tokens)"}
        if prompt.get("tools"):
            name = prompt["tools"][0]["function"]["name"]
            call = json.dumps({"name": name, "arguments": {"q": said}})
            return {**result, "text": f"Let me look.\n<tool_call>\n{call}\n</tool_call>"}
        if "json_schema" in prompt["generate"]:
            return {**result, "text": json.dumps({"said": said})}
        return {**result, "text": f"echo: {said}"}

    def step(self, run):
        """The job answers (or refuses) everything in its inbox."""
        if self.paused or run["status"]["state"] in ("stopped", "failed"):
            return
        for request_id, prompts in list(run["inbox"].items()):
            del run["inbox"][request_id]
            if any("BAD" in (p["messages"][-1].get("content") or "") for p in prompts):
                run["outbox"][request_id] = (
                    "refused",
                    {"status": "refused", "error": "prompt 'p0': bad"},
                )
                run["events"].append({"request": request_id, "status": "refused"})
                continue
            run["outbox"][request_id] = ("answered", [self.answer(p) for p in prompts])
            run["events"].append({"request": request_id, "status": "answered"})


class FakeBlythe:
    def __init__(self, account):
        self.account, self.open = account, True

    def alive(self):
        return self.open

    def close(self):
        self.open = False

    def start_server(self, run_id, request, hours, gpus=None, mem_gb=None):
        self.account.next_job += 1
        self.account.resources[run_id] = {"gpus": gpus, "mem_gb": mem_gb}
        state = "failed" if self.account.fail_every_start else "waiting"
        self.account.runs[run_id] = {
            "request": request,
            "inbox": {},
            "outbox": {},
            "events": [],
            "status": {"state": state, "error": self.account.fail_every_start},
            "job": str(self.account.next_job),
            "requests": [],
        }
        return {
            "run_dir": f"/home/x/narratrace/runs/{run_id}",
            "slurm_job_id": str(self.account.next_job),
        }

    def add_request(self, run_id, request_id, prompts_gz, callback_url=None):
        run = self.account.runs[run_id]
        if run["status"]["state"] == "stopped":
            raise BlytheError(
                f"The server in {run_id} has stopped or been asked to stop (stopped): start a new one."
            )
        prompts = [json.loads(line) for line in gzip.decompress(prompts_gz).decode().splitlines()]
        run["inbox"][request_id] = prompts
        run["requests"].append((request_id, prompts))

    def events(self, run_id, offset=0):
        run = self.account.runs[run_id]
        self.account.step(run)
        return {
            "events": run["events"][offset:],
            "offset": len(run["events"]),
            "status": dict(run["status"]),
        }

    def collect(self, run_id, request_id):
        state, payload = self.account.runs[run_id]["outbox"][request_id]
        if state == "refused":
            return {"state": "refused", "error": payload}
        lines = "".join(json.dumps(r) + "\n" for r in payload)
        return {
            "state": "answered",
            "summary": {"n_prompts": len(payload)},
            "results": gzip.compress(lines.encode()),
        }

    def job_state(self, job_id):
        return {"finished": False, "state": "RUNNING"}


def windmill_stand_in(request: httpx.Request) -> httpx.Response:
    token = request.headers.get("authorization", "")[7:]
    user = TOKENS.get(token)
    if not user:
        return httpx.Response(401, json={"error": "bad token"})
    if request.url.path == "/api/w/dhr/users/whoami":
        return httpx.Response(200, json={"username": user})
    prefix = f"/api/w/dhr/variables/get_value/u/{user}/"
    if request.url.path.startswith(prefix):
        return httpx.Response(200, json=f"value of {request.url.path[len(prefix) :]}")
    return httpx.Response(403, json={"error": "not yours"})


@pytest.fixture
def setup(tmp_path):
    accounts = {user: Account() for user in TOKENS.values()}
    settings = Settings(
        batch_seconds=0.05,
        check_seconds=0.01,
        timeout_seconds=5,
        slurm_check_seconds=0.2,
        max_errors=2,
    )
    windmill = Windmill("http://windmill", "dhr", transport=httpx.MockTransport(windmill_stand_in))

    def connect(user, token):
        accounts[user].logins.append(token)
        return FakeBlythe(accounts[user])

    gateway = Gateway(windmill, settings, tmp_path / "data", connect=connect)
    client = TestClient(create_app(gateway))
    yield client, accounts, gateway
    for session in gateway.sessions.values():
        session.close()


def chat(client, token="tok-ks", **body):
    body = {"model": MODEL, "messages": [{"role": "user", "content": "hello"}], **body}
    return client.post(
        "/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {token}"}
    )


def all_prompts(account):
    return [p for run in account.runs.values() for _, prompts in run["requests"] for p in prompts]


def test_a_call_runs_on_the_caller_s_own_account(setup):
    client, accounts, gateway = setup
    reply = chat(client)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["object"] == "chat.completion" and body["model"] == MODEL
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "echo: hello"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert accounts["ks0610"].logins == ["tok-ks"] and not accounts["rshields2004"].logins
    (run,) = accounts["ks0610"].runs.values()
    assert run["request"]["model"] == MODEL and run["request"]["idle_minutes"] == 30
    assert all_prompts(accounts["ks0610"])[0]["generate"] == {"max_tokens": 1024}

    headers = {"Authorization": "Bearer tok-ks"}
    status = client.get("/status", headers=headers).json()
    assert status["user"] == "ks0610" and status["run_id"].startswith("gw-")
    assert client.get("/usage", headers=headers).json()["calls"] == 1
    assert client.get("/usage", headers={"Authorization": "Bearer tok-rowan"}).json()["calls"] == 0
    assert client.get("/v1/models", headers=headers).json()["data"][0]["id"] == MODEL
    assert client.get("/health").json()["ok"]


def test_two_people_get_their_own_jobs(setup):
    client, accounts, _ = setup
    assert chat(client, "tok-ks").status_code == 200
    assert chat(client, "tok-rowan").status_code == 200
    assert len(accounts["ks0610"].runs) == 1 and len(accounts["rshields2004"].runs) == 1
    assert accounts["rshields2004"].logins == ["tok-rowan"]


def test_without_a_windmill_token_nothing_happens(setup):
    client, accounts, _ = setup
    assert client.post("/v1/chat/completions", json={}).status_code == 401
    assert chat(client, "not-a-token").status_code == 401
    assert client.get("/status").status_code == 401
    assert all(not a.logins for a in accounts.values())


@pytest.mark.parametrize(
    "body, status",
    [
        ({"model": "gpt-5"}, 404),
        ({"stream": True}, 400),
        ({"n": 2}, 400),
        ({"tool_choice": "required", "tools": []}, 400),
        (
            {
                "messages": [
                    {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
                ]
            },
            400,
        ),
        ({"messages": []}, 400),
    ],
)
def test_what_the_gateway_can_t_do_is_refused(setup, body, status):
    client, accounts, _ = setup
    assert chat(client, **body).status_code == status
    assert not accounts["ks0610"].runs  # refused before anything reached Blythe


def test_tool_calls_come_back_in_openai_s_format(setup):
    client, accounts, _ = setup
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    reply = chat(client, tools=tools).json()
    message = reply["choices"][0]["message"]
    assert reply["choices"][0]["finish_reason"] == "tool_calls"
    assert message["content"] == "Let me look."
    (call,) = message["tool_calls"]
    assert call["type"] == "function" and call["function"]["name"] == "lookup"
    assert json.loads(call["function"]["arguments"]) == {"q": "hello"}
    assert all_prompts(accounts["ks0610"])[0]["tools"] == tools

    chat(client, tools=tools, tool_choice="none")
    assert "tools" not in all_prompts(accounts["ks0610"])[-1]


def test_json_answers_and_label_answers(setup):
    client, accounts, _ = setup
    schema = {"type": "object", "properties": {"said": {"type": "string"}}}
    reply = chat(
        client,
        response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": schema}},
    )
    assert json.loads(reply.json()["choices"][0]["message"]["content"]) == {"said": "hello"}
    assert all_prompts(accounts["ks0610"])[-1]["generate"]["json_schema"] == schema

    reply = chat(client, labels=["A", "B"]).json()
    assert reply["choices"][0]["message"]["content"] == "A"
    assert reply["choices"][0]["message"]["label_probabilities"] == {"A": 0.9}
    assert all_prompts(accounts["ks0610"])[-1]["labels"] == ["A", "B"]


def test_errors_from_the_job_become_openai_errors(setup):
    client, _, _ = setup
    reply = chat(client, messages=[{"role": "user", "content": "TOO LONG"}])
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "context_length_exceeded"
    reply = chat(client, messages=[{"role": "user", "content": "BAD"}])
    assert reply.status_code == 400 and "bad" in reply.json()["error"]["message"]


def test_calls_arriving_together_go_to_the_gpu_together(setup):
    client, accounts, gateway = setup
    gateway.settings.batch_seconds = 0.5  # wide enough for six threads, however busy the machine
    replies = {}

    def call(n):
        replies[n] = chat(client, messages=[{"role": "user", "content": f"question {n}"}]).json()

    threads = [threading.Thread(target=call, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {n: r["choices"][0]["message"]["content"] for n, r in replies.items()} == {
        n: f"echo: question {n}" for n in range(6)
    }
    (run,) = accounts["ks0610"].runs.values()
    assert len(run["requests"]) == 1  # six calls, sent as one request


def test_a_job_that_stopped_is_replaced(setup):
    client, accounts, _ = setup
    assert chat(client).status_code == 200
    (first,) = accounts["ks0610"].runs.values()
    first["status"]["state"] = "stopped"  # it went idle and stopped
    assert chat(client).status_code == 200
    assert len(accounts["ks0610"].runs) == 2


def test_calls_waiting_on_a_job_that_ends_go_to_a_new_one(setup):
    client, accounts, gateway = setup
    account = accounts["ks0610"]
    account.paused = True
    reply = {}
    thread = threading.Thread(target=lambda: reply.update(r=chat(client)))
    thread.start()
    deadline = time.monotonic() + 3
    while not account.runs and time.monotonic() < deadline:
        time.sleep(0.01)
    (first,) = account.runs.values()
    first["status"]["state"] = "stopped"  # e.g. its time limit came
    account.paused = False
    thread.join()
    assert reply["r"].status_code == 200 and len(account.runs) == 2


def test_a_job_that_keeps_failing_fails_the_call_with_its_error(setup):
    client, accounts, _ = setup
    accounts["ks0610"].fail_every_start = "CUDA out of memory"
    reply = chat(client)
    assert reply.status_code == 502 and "CUDA out of memory" in reply.json()["error"]["message"]
    assert len(accounts["ks0610"].runs) == 2  # tried a second job before giving up


def test_a_call_that_waits_too_long_gets_a_timeout(setup):
    client, accounts, gateway = setup
    gateway.settings.timeout_seconds = 0.3
    accounts["ks0610"].paused = True
    reply = chat(client)
    assert reply.status_code == 504 and "GPU" in reply.json()["error"]["message"]


def test_the_openai_client_works_against_it(setup):
    client, _, _ = setup
    openai = pytest.importorskip("openai")
    llm = openai.OpenAI(base_url="http://testserver/v1", api_key="tok-ks", http_client=client)
    reply = llm.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "hi"}])
    assert reply.choices[0].message.content == "echo: hi"
    tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    reply = llm.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": "x"}], tools=tools
    )
    assert reply.choices[0].message.tool_calls[0].function.name == "lookup"
    with pytest.raises(openai.BadRequestError):
        llm.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "TOO LONG"}])
    with pytest.raises(openai.AuthenticationError):
        openai.OpenAI(
            base_url="http://testserver/v1", api_key="nope", http_client=client
        ).models.list()


def test_windmill_reads_only_the_caller_s_own_variables(setup):
    _, _, gateway = setup
    assert gateway.windmill.user("tok-ks") == "ks0610"
    get = gateway.windmill.variables("tok-ks")
    assert get("u/ks0610/blythe_username") == "value of blythe_username"
    with pytest.raises(httpx.HTTPStatusError):
        get("u/rshields2004/blythe_ssh_key")  # Windmill refuses someone else's


# ---------------------------------------------------------------- the OpenAI translation


def test_requests_become_llm_serve_prompts():
    prompt = to_prompt(
        {
            "messages": [
                {"role": "developer", "content": "Be brief."},
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "f", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "done"},
            ],
            "max_completion_tokens": 50,
            "temperature": 0.2,
            "stop": "END",
            "seed": 3,
            "response_format": {"type": "json_object"},
            "thinking": False,
            "frequency_penalty": 0,
            "user": "ignored",
        },
        "p",
    )
    assert prompt["messages"][0] == {"role": "system", "content": "Be brief."}
    assert prompt["messages"][1]["content"] == "ab"
    assert prompt["messages"][2]["tool_calls"][0]["function"]["name"] == "f"
    assert prompt["messages"][3] == {"role": "tool", "content": "done", "tool_call_id": "c1"}
    assert prompt["generate"] == {
        "max_tokens": 50,
        "temperature": 0.2,
        "seed": 3,
        "stop": ["END"],
        "json_schema": {"type": "object"},
    }
    for bad in (
        {"messages": [{"role": "user", "content": "x"}], "frequency_penalty": 0.5},
        {"messages": [{"role": "user", "content": "x"}], "labels": ["A"], "tools": [{}]},
        {"messages": [{"role": "tool", "content": "x"}], "labels": ["A"]},
        {"messages": [{"role": "user", "content": "x"}], "response_format": {"type": "xml"}},
        {"messages": [{"role": "robot", "content": "x"}]},
    ):
        with pytest.raises(RequestError):
            to_prompt(bad, "p")


def test_qwen_tool_calls_are_read():
    text = 'Two calls.\n<tool_call>\n{"name": "a", "arguments": {"x": 1}}\n</tool_call>\n<tool_call>{"name": "b", "arguments": "{\\"y\\": 2}"}</tool_call>'
    rest, calls = parse_tool_calls(text)
    assert rest == "Two calls."
    assert [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in calls] == [
        ("a", {"x": 1}),
        ("b", {"y": 2}),
    ]
    assert len({c["id"] for c in calls}) == 2
    assert parse_tool_calls("<tool_call>not json</tool_call>") == (
        "<tool_call>not json</tool_call>",
        [],
    )
    only = parse_tool_calls('<tool_call>{"name": "a", "arguments": {}}</tool_call>')
    assert only[0] is None and only[1][0]["function"]["arguments"] == "{}"


def test_thinking_comes_back_separately():
    result = {
        "text": "Answer.",
        "reasoning": "Because.",
        "finish_reason": "stop",
        "prompt_tokens": 1,
        "completion_tokens": 2,
    }
    message = to_completion(result, MODEL, had_tools=False)["choices"][0]["message"]
    assert message == {"role": "assistant", "content": "Answer.", "reasoning_content": "Because."}


# ---------------------------------------------------------------- the team's helpers


def test_the_helper_and_the_windmill_chat_script(setup, monkeypatch):
    client, accounts, _ = setup
    openai = pytest.importorskip("openai")
    from f.dhr import llm
    from f.dhr.tools import llm_chat

    monkeypatch.delenv("WM_TOKEN", raising=False)
    monkeypatch.delenv("WINDMILL_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="No Windmill token"):
        llm.connection()
    monkeypatch.setenv("WM_TOKEN", "tok-ks")  # as inside a Windmill run
    assert llm.connection() == ("http://llm-gateway:8000/v1", "tok-ks")
    monkeypatch.setenv("NARRATRACE_LLM_URL", "http://127.0.0.1:8100/v1")  # a laptop's tunnel
    assert llm.connection()[0] == "http://127.0.0.1:8100/v1"

    def gateway_client():
        return openai.OpenAI(base_url="http://testserver/v1", api_key="tok-ks", http_client=client)

    monkeypatch.setattr(llm_chat, "client", gateway_client)
    assert llm_chat.main(question="hello")["answer"] == "echo: hello"
    labelled = llm_chat.main(question="A or B?", labels=["A", "B"])
    assert labelled["answer"] == "A" and labelled["label_probabilities"] == {"A": 0.9}
    assert llm_chat.main(question="x", json_schema={"type": "object"})["answer"] == {"said": "x"}
    assert accounts["ks0610"].logins and not accounts["rshields2004"].logins


def test_a_call_that_timed_out_doesn_t_break_the_session(setup):
    client, accounts, gateway = setup
    gateway.settings.timeout_seconds = 0.3
    accounts["ks0610"].paused = True
    assert chat(client).status_code == 504  # cancelled while the job was busy
    gateway.settings.timeout_seconds = 5
    (run,) = accounts["ks0610"].runs.values()
    run["status"]["state"] = "stopped"  # its job then ends, with the cancelled call waiting
    accounts["ks0610"].paused = False
    reply = chat(client)
    assert (
        reply.status_code == 200
        and reply.json()["choices"][0]["message"]["content"] == "echo: hello"
    )
    assert gateway.sessions["ks0610"].thread.is_alive()


def test_the_job_is_full_precision_qwen_split_over_two_gpus(setup):
    """The default profile (26 Nov 2026): Qwen3.8-27B in BF16 on two L40s, with the settings
    of the first two-GPU job that worked, and 131,072-token prompts."""
    client, accounts, gateway = setup
    assert chat(client).status_code == 200
    ((run_id, run),) = accounts["ks0610"].runs.items()
    assert MODEL == "Qwen/Qwen3.8-27B"
    assert accounts["ks0610"].resources[run_id] == {"gpus": 2, "mem_gb": 120}
    request = run["request"]
    assert request["model"] == "Qwen/Qwen3.8-27B" and request["max_model_len"] == 131072
    assert request["max_num_seqs"] == 64
    assert request["engine"] == {
        "tensor_parallel_size": 2,
        "enforce_eager": True,
        "disable_custom_all_reduce": True,
    }


def test_calls_naming_the_old_fp8_model_still_work(setup):
    """f/dhr/llm.py and everyone's WeKnora model entry name Qwen/Qwen3.8-27B-FP8."""
    client, accounts, gateway = setup
    reply = chat(client, model="Qwen/Qwen3.8-27B-FP8")
    assert reply.status_code == 200, reply.text
    assert reply.json()["model"] == "Qwen/Qwen3.8-27B"  # the model that answered
    assert chat(client, model="gpt-4o").status_code == 404


def test_one_setting_switches_back_to_fp8_on_one_gpu(monkeypatch):
    monkeypatch.setenv("GATEWAY_PROFILE", "fp8-1gpu")
    settings = settings_from_env()
    assert settings.model == "Qwen/Qwen3.8-27B-FP8" and settings.max_model_len == 32768
    assert settings.job_resources == {"gpus": None, "mem_gb": None}  # the job file's own
    request = settings.server_request()
    assert "engine" not in request and request["max_num_seqs"] == 128

    monkeypatch.setenv("GATEWAY_MAX_MODEL_LEN", "65536")  # one setting can still be changed
    assert settings_from_env().max_model_len == 65536
    monkeypatch.setenv("GATEWAY_PROFILE", "fp16")
    with pytest.raises(ValueError, match="GATEWAY_PROFILE"):
        settings_from_env()
    assert set(PROFILES) == {"bf16-2gpu", "fp8-1gpu"}
