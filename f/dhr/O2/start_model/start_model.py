# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "openai==3.51.0",
# ]
# ///
"""O2 stage 2: start the model and wait until it answers (on your own Blythe account).

Sends one tiny question through the LLM gateway, as the person running the flow. If their
model job isn't running, the gateway starts it on Blythe and the answer comes once SLURM has
given it a GPU and the model has loaded (usually a few minutes; up to an hour is allowed).
WeKnora's own requests then find the model ready, instead of timing out while it loads.
"""

from __future__ import annotations

import time

from ...llm import MODEL, client


def main():
    started = time.monotonic()
    reply = client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        max_tokens=5,
    )
    return {
        "model": reply.model,  # the model that answered
        "answer": reply.choices[0].message.content,
        "seconds_waited": round(time.monotonic() - started),
    }
