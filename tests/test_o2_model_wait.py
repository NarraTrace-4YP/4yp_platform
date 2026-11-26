"""f/dhr/O2/wait_for_model: one check of the model job, run every few minutes by the O2 flow
(26 Nov 2026: SLURM can keep a two-GPU job queued for days)."""

from types import SimpleNamespace

import pytest

from f.dhr.O2.wait_for_model import wait_for_model as w


def fake_client(answers):
    def create(**kwargs):
        answers.append(kwargs)
        message = SimpleNamespace(content="ready")
        return SimpleNamespace(model="Qwen/Qwen3.8-27B", choices=[SimpleNamespace(message=message)])

    completions = SimpleNamespace(create=create)
    return lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_a_queued_job_is_not_ready_and_says_why(monkeypatch, capsys):
    queued = {"run_id": "gw-1", "slurm_job_id": "77", "job_state": "submitted"}
    queued["slurm"] = {"state": "PENDING", "reason": "Resources"}
    monkeypatch.setattr(w, "gateway_refresh", lambda model: queued)
    found = w.main()
    assert found["ready"] is False and found["slurm_reason"] == "Resources"
    assert "waiting for Blythe GPUs (SLURM: PENDING, Resources)" in capsys.readouterr().out


def test_an_answering_job_is_asked_one_question(monkeypatch):
    answers = []
    monkeypatch.setattr(
        w, "gateway_refresh", lambda model: {"run_id": "gw-1", "job_state": "waiting"}
    )
    monkeypatch.setattr(w, "client", fake_client(answers))
    found = w.main("qwen-27b-bf16")
    assert found["ready"] is True and found["model"] == "qwen-27b-bf16"
    assert len(answers) == 1 and answers[0]["max_tokens"] == 5
    assert answers[0]["model"] == "qwen-27b-bf16"  # the build's model, not the default


def test_an_ended_job_is_replaced_and_a_failed_one_stops_the_flow(monkeypatch):
    started = []
    monkeypatch.setattr(w, "gateway_refresh", lambda model: {"run_id": None, "last_failure": None})
    monkeypatch.setattr(
        w,
        "gateway_start",
        lambda model: started.append(model) or {"run_id": "gw-2", "job_state": "submitted"},
    )
    assert w.main("qwen-27b-fp8")["run_id"] == "gw-2" and started == ["qwen-27b-fp8"]

    failed = {"run_id": None, "last_failure": "the 2 GPUs on sea004 couldn't talk to each other"}
    monkeypatch.setattr(w, "gateway_refresh", lambda model: failed)
    with pytest.raises(RuntimeError, match="couldn't talk"):
        w.main()
    assert started == ["qwen-27b-fp8"]  # not submitted again


def test_a_model_is_found_by_key_hugging_face_name_or_default(monkeypatch):
    from f.dhr import llm

    listed = {
        "default": "qwen-27b-bf16",
        "data": [
            {"id": "qwen-27b-bf16", "model": "Qwen/Qwen3.8-27B"},
            {"id": "qwen-27b-bf16-graphs", "model": "Qwen/Qwen3.8-27B"},
            {"id": "qwen-27b-fp8", "model": "Qwen/Qwen3.8-27B-FP8"},
        ],
    }
    monkeypatch.setattr(llm, "models", lambda: listed)
    assert llm.model_info()["id"] == llm.model_info("default")["id"] == "qwen-27b-bf16"
    assert llm.model_info("qwen-27b-fp8")["model"] == "Qwen/Qwen3.8-27B-FP8"
    assert llm.model_info("Qwen/Qwen3.8-27B-FP8")["id"] == "qwen-27b-fp8"  # builds before 26 Nov
    assert llm.model_info("Qwen/Qwen3.8-27B")["id"] == "qwen-27b-bf16"
    with pytest.raises(ValueError, match="qwen-27b-fp8"):
        llm.model_info("llama")
    assert llm.served_model("qwen-27b-fp8") == "Qwen/Qwen3.8-27B-FP8"
    assert llm.served_model("llama") == "llama"
