"""Translating between OpenAI's chat-completions format and llm_serve's prompts.

The gateway speaks the part of OpenAI's API that the team's code uses (the openai client,
or langchain-openai's ChatOpenAI): chat messages, max_tokens, temperature, top_p, stop,
seed, response_format (JSON or a JSON schema), and tools. Anything it can't honour is
refused with a clear error rather than silently ignored.

Two additions of our own, sent as extra fields (extra_body= in the openai client):
    "labels": ["A", "B", ...]   answer with one of these labels (llm_batch's label prompts),
                                giving each label's probability in the reply
    "thinking": true            let Qwen think first; its thinking comes back separately as
                                message.reasoning_content
"""

from __future__ import annotations

import json
import re
import secrets
import time

MAX_TOKENS_DEFAULT = 1024
TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
IGNORED = {  # accepted and ignored: they don't change the answer here
    "user",
    "metadata",
    "store",
    "parallel_tool_calls",
    "service_tier",
    "stream_options",
}
UNSUPPORTED_IF_SET = {  # refused unless left at their "off" value
    "frequency_penalty": (None, 0, 0.0),
    "presence_penalty": (None, 0, 0.0),
    "logit_bias": (None, {}),
    "logprobs": (None, False),
    "top_logprobs": (None, 0),
    "n": (None, 1),
    "stream": (None, False),
}


class RequestError(ValueError):
    """The request asks for something the gateway can't do; becomes an HTTP 400."""

    def __init__(self, message: str, code: str = "invalid_request_error"):
        super().__init__(message)
        self.code = code


def _text(content, where: str) -> str | None:
    """A message's content: text, or a list of text parts (images aren't supported)."""
    if content is None or isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                raise RequestError(f"{where}: only text content is supported")
            parts.append(str(part.get("text", "")))
        return "".join(parts)
    raise RequestError(f"{where}: content must be text")


def _messages(messages, labels: bool) -> list[dict]:
    if not isinstance(messages, list) or not messages:
        raise RequestError("messages must be a non-empty list")
    out = []
    for n, message in enumerate(messages):
        where = f"messages[{n}]"
        if not isinstance(message, dict):
            raise RequestError(f"{where} must be an object")
        role = message.get("role")
        if role == "developer":
            role = "system"  # OpenAI's newer name for it
        if role not in ("system", "user", "assistant", "tool"):
            raise RequestError(f"{where}: role {role!r} isn't supported")
        content = _text(message.get("content"), where)
        clean = {"role": role, "content": content}
        if message.get("tool_calls"):
            clean["tool_calls"] = [
                {
                    "id": str(call.get("id", "")),
                    "type": "function",
                    "function": {
                        "name": call["function"]["name"],
                        "arguments": call["function"].get("arguments", "{}"),
                    },
                }
                for call in message["tool_calls"]
            ]
        if role == "tool":
            clean["tool_call_id"] = str(message.get("tool_call_id", ""))
        if labels and (role == "tool" or "tool_calls" in clean or content is None):
            raise RequestError("a labels request can only have system, user and assistant text")
        if content is None and "tool_calls" not in clean:
            clean["content"] = ""
        out.append(clean)
    return out


def _schema(response_format) -> dict | None:
    if response_format in (None, {}):
        return None
    kind = response_format.get("type") if isinstance(response_format, dict) else None
    if kind == "text":
        return None
    if kind == "json_object":
        return {"type": "object"}
    if kind == "json_schema":
        schema = (response_format.get("json_schema") or {}).get("schema")
        if not isinstance(schema, dict):
            raise RequestError("response_format json_schema needs a schema object")
        return schema
    raise RequestError(f"response_format type {kind!r} isn't supported")


def to_prompt(body: dict, prompt_id: str) -> dict:
    """An llm_serve prompt from an OpenAI chat-completions request body."""
    for name, allowed in UNSUPPORTED_IF_SET.items():
        if name in body and body[name] not in allowed:
            raise RequestError(f"{name} isn't supported by this gateway")
    labels = body.get("labels")
    messages = _messages(body.get("messages"), labels is not None)
    if labels is not None:
        if body.get("tools") or body.get("response_format"):
            raise RequestError("labels can't be combined with tools or response_format")
        return {"id": prompt_id, "messages": messages, "labels": labels}

    generate: dict = {}
    max_tokens = body.get("max_completion_tokens") or body.get("max_tokens")
    generate["max_tokens"] = int(max_tokens) if max_tokens else MAX_TOKENS_DEFAULT
    for name in ("temperature", "top_p", "seed"):
        if body.get(name) is not None:
            generate[name] = body[name]
    stop = body.get("stop")
    if stop:
        generate["stop"] = [stop] if isinstance(stop, str) else list(stop)
    schema = _schema(body.get("response_format"))
    if schema is not None:
        generate["json_schema"] = schema
    if body.get("thinking"):
        generate["thinking"] = True
    prompt = {"id": prompt_id, "messages": messages, "generate": generate}

    tools = body.get("tools")
    choice = body.get("tool_choice")
    if choice not in (None, "auto", "none"):
        raise RequestError("tool_choice can only be auto or none here")
    if tools and choice != "none":
        prompt["tools"] = tools
    return prompt


def parse_tool_calls(text: str) -> tuple[str | None, list[dict]]:
    """Qwen writes tool calls as <tool_call>{"name": ..., "arguments": {...}}</tool_call>
    blocks; this turns them into OpenAI tool_calls and returns the text left around them."""
    calls = []
    for raw in TOOL_CALL.findall(text):
        try:
            call = json.loads(raw)
            name = call["name"]
        except (ValueError, KeyError, TypeError):
            continue  # not a call after all: it stays in the text
        arguments = call.get("arguments", {})
        calls.append(
            {
                "id": "call_" + secrets.token_hex(8),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                },
            }
        )
    if not calls:
        return text, []
    rest = TOOL_CALL.sub("", text).strip()
    return rest or None, calls


def to_completion(result: dict, model: str, had_tools: bool) -> dict:
    """An OpenAI chat-completion response from one llm_serve answer."""
    usage = {
        "prompt_tokens": result.get("prompt_tokens") or 0,
        "completion_tokens": result.get("completion_tokens") or 0,
    }
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    message: dict = {"role": "assistant"}
    if "probabilities" in result:  # a labels request
        message["content"] = result.get("answer")
        message["label_probabilities"] = result.get("probabilities")
        finish = "stop"
    else:
        text = result.get("text") or ""
        content, calls = parse_tool_calls(text) if had_tools else (text, [])
        message["content"] = content
        if calls:
            message["tool_calls"] = calls
        if result.get("reasoning") is not None:
            message["reasoning_content"] = result["reasoning"]
        finish = "tool_calls" if calls else (result.get("finish_reason") or "stop")
    return {
        "id": "chatcmpl-" + secrets.token_hex(12),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage,
    }
