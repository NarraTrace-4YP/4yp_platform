# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "openai==3.51.0",
# ]
# ///
"""LLM chat: ask the team's model one question through the LLM gateway, on your own Blythe account.

A quick way to try the gateway from Windmill: it sends the question as you (with this run's
own Windmill token), the gateway starts your model job on Blythe if it isn't running, and
the answer comes back. The first question after the model has been idle waits for a GPU and
for the model to load (a few minutes); later ones take seconds.

labels: answer with one of these (the reply gives each one's probability). json_schema: the
answer must be JSON matching this schema. Leave both empty for an ordinary answer.
(The default question has no brackets in it: Windmill can't read a script whose default
text contains a ")".)
"""

from __future__ import annotations

import json

from ..llm import MODEL, client


def main(
    question: str = "Which of these is a colour: blue or seven? Answer with one word.",
    system: str = "",
    labels: list[str] | None = None,
    json_schema: dict | None = None,
    max_tokens: int = 300,
    model: str = "default",
):
    """model: a key from the gateway's catalogue (e.g. qwen-27b-fp8), or "default"."""
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": question})
    options: dict = {}
    if labels:
        options["extra_body"] = {"labels": labels}
    elif json_schema:
        options["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": json_schema},
        }
    reply = client().chat.completions.create(
        model=model or MODEL, messages=messages, max_tokens=max_tokens, **options
    )
    message = reply.choices[0].message
    answer = message.content
    if json_schema and not labels and answer:
        answer = json.loads(answer)
    extra = message.model_extra or {}
    return {
        "answer": answer,
        "label_probabilities": extra.get("label_probabilities"),
        "model": reply.model,
        "finish_reason": reply.choices[0].finish_reason,
        "usage": reply.usage.model_dump() if reply.usage else None,
    }
