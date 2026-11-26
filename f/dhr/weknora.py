"""WeKnora, the team's LLM wiki website, through its API, as the person running the script.

WeKnora (docs/weknora.md) runs on the project server; Windmill scripts reach its API at
http://weknora-app:8080 on the server's Docker network.

The team works in one shared WeKnora space (Rowan, 24 Nov 2026), reached with that space's
API key, kept in the secret Windmill variable f/dhr/weknora_api_key. Every wiki lives there,
so the whole team can open every build in WeKnora.

The model still runs on the account of whoever starts the work: WeKnora calls the LLM
gateway with that person's Windmill token as the API key, and the gateway logs in to Blythe
as them. WeKnora.model_entry() makes each person's entry for every model a build chooses
(26 Nov 2026), so nobody adds models in WeKnora by hand: model name = the gateway's key for
it (e.g. qwen-27b-bf16), display name "<label> (<username>)", the model's context window
and concurrency, and as API key a Windmill token made for them for this (valid
TOKEN_DAYS days, renewed by the pipeline a month before it ends; they see it in Windmill:
Account settings > Tokens). Someone who already has an entry of their own ("Qwen
(<username>)") has it copied instead, token and all. So a build by markcoding05 on
qwen-27b-bf16 uses "Qwen3.8 27B BF16 (markcoding05)", which runs on markcoding05's Blythe
account, never on someone else's.

A personal key in u/<you>/weknora_api_key (an API key from your own space) is still used if
the shared one is missing; then any chat model named after the gateway model's key will do.

    from .weknora import WeKnora                # (from ..weknora in a subfolder, etc.)

    wk = WeKnora()
    kb = wk.create_knowledge_base(name="pilot", chat_model_id=..., ...)
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta

BASE_URL = "http://weknora-app:8080/api/v1"
KEY_VARIABLE = "weknora_api_key"
SHARED_KEY_PATH = f"f/dhr/{KEY_VARIABLE}"
TIMEOUT_SECONDS = 120
GATEWAY_URL = "http://llm-gateway:8000/v1"  # what WeKnora calls the models through
TOKEN_DAYS = 365  # how long the Windmill token in a made entry lasts
RENEW_DAYS = 30  # renew it when this close to its end
VALID_UNTIL = re.compile(r"valid until (\d{4}-\d{2}-\d{2})")


def make_gateway_token() -> tuple[str, date]:
    """A new Windmill token for the person running the script, for their WeKnora model
    entries (the gateway knows them, and reaches their Blythe variables, by it)."""
    import wmill

    until = date.today() + timedelta(days=TOKEN_DAYS)
    return wmill.create_token(duration=timedelta(days=TOKEN_DAYS)), until


def _token_note(until: date) -> str:
    return (
        f" Its API key is a Windmill token made for you, valid until {until.isoformat()}"
        " (Windmill: Account settings > Tokens); the pipeline renews it."
    )


class WeKnoraError(RuntimeError):
    pass


def runner() -> str | None:
    return os.environ.get("WM_USERNAME")


def model_label(user: str) -> str:
    """The display name each person gives their own model entry in the shared space."""
    return f"Qwen ({user})"


def _variable(path: str) -> str | None:
    import wmill

    try:
        value = wmill.get_variable(path)
    except Exception:  # noqa: BLE001 (wmill raises a plain Exception when it's missing)
        return None
    return value.strip() if value and value.strip() else None


def key_source() -> tuple[str, bool]:
    """The WeKnora API key to use, and whether it is the team's shared space key."""
    key = os.environ.get("WEKNORA_API_KEY")
    if key:
        return key, os.environ.get("WEKNORA_SHARED", "1") == "1"
    user = runner()
    if not user:
        raise WeKnoraError("No WeKnora API key: set WEKNORA_API_KEY outside Windmill.")
    shared = _variable(SHARED_KEY_PATH)
    if shared:
        return shared, True
    personal = _variable(f"u/{user}/{KEY_VARIABLE}")
    if personal:
        return personal, False
    raise WeKnoraError(
        f"No WeKnora API key: neither the team's {SHARED_KEY_PATH} nor your own"
        f" u/{user}/{KEY_VARIABLE} is set (or you can't read them). docs/weknora.md says how."
    )


def api_key() -> str:
    return key_source()[0]


def _data(body):
    """WeKnora wraps most answers as {"success": true, "data": ...}; some are bare."""
    if isinstance(body, dict) and "data" in body and ("success" in body or len(body) <= 5):
        return body["data"]
    return body


class WeKnora:
    """A small client for the parts of WeKnora's API that O2 uses."""

    def __init__(
        self, key: str | None = None, base_url: str | None = None, client=None, shared: bool = True
    ):
        import httpx

        if key is None and client is None:
            key, shared = key_source()
        self.shared = shared  # the team's shared space (True) or someone's own space (False)
        self.base_url = (base_url or os.environ.get("WEKNORA_URL") or BASE_URL).rstrip("/")
        self.http = client or httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": key or ""},
            timeout=TIMEOUT_SECONDS,
        )

    def request(self, method: str, path: str, *, retries: int = 3, **options):
        """One API call, retried on connection errors and 5xx answers. Returns the JSON body."""
        import httpx

        for attempt in range(retries + 1):
            try:
                response = self.http.request(method, path, **options)
            except httpx.TransportError as error:
                if attempt == retries:
                    raise WeKnoraError(f"{method} {path}: can't reach WeKnora ({error})") from error
                time.sleep(2 * (attempt + 1))
                continue
            if response.status_code >= 500 and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            if response.status_code == 401:
                raise WeKnoraError(
                    f"{method} {path}: WeKnora refused the API key (401). Check that"
                    f" {SHARED_KEY_PATH} (or u/<you>/{KEY_VARIABLE}) holds a full-access key"
                    " made in WeKnora (Settings > Publish & Integrations > API Integration);"
                    " it starts with sk-."
                )
            if response.status_code >= 400:
                raise WeKnoraError(
                    f"{method} {path}: WeKnora answered {response.status_code}: {response.text[:500]}"
                )
            return response.json() if response.content else None
        raise AssertionError("unreachable")

    # --- models -------------------------------------------------------------------------

    def models(self) -> list[dict]:
        return _data(self.request("GET", "/models")) or []

    def chat_model(self, name: str, user: str | None = None) -> dict:
        """The runner's own entry for the team model (model name `name`).

        In the shared space that is the chat model whose display name is "Qwen (<user>)": the
        one that runs on that person's Blythe account. Someone else's entry is never used. In
        a personal space any chat model with that model name will do.
        """
        models = self.models()
        user = user or runner()
        chat = [m for m in models if m.get("type") == "KnowledgeQA"]
        if self.shared:
            label = model_label(user or "?")
            found = [m for m in chat if m.get("name") == name and m.get("display_name") == label]
            if not found:
                seen = (
                    ", ".join(
                        f"{m.get('display_name') or '(no display name)'!r} [{m.get('name')}]"
                        for m in chat
                    )
                    or "none at all"
                )
                raise WeKnoraError(
                    f"The team's WeKnora space has no model entry of yours: a chat model with"
                    f" display name {label!r} and model name {name!r} (chat models there:"
                    f" {seen}). Add it in the team space: Settings > Models > Add Model, chat,"
                    " source API, provider Custom (OpenAI-compatible), base URL"
                    " http://llm-gateway:8000/v1, your own Windmill token as the API key, model"
                    f" name {name}, display name {label}. Your entry is what makes the work run"
                    " on your own Blythe account."
                )
            return found[0]
        found = [m for m in chat if name in (m.get("name"), m.get("display_name"))]
        if not found:
            seen = (
                ", ".join(
                    f"{m.get('name') or m.get('display_name')!r} ({m.get('type')})" for m in models
                )
                or "none at all"
            )
            raise WeKnoraError(
                f"WeKnora has no chat model named {name!r} in the space your API key belongs to"
                f" (models there: {seen}). Add it in that space's model settings as a chat model"
                " (docs/weknora.md: provider Custom (OpenAI-compatible), base URL"
                " http://llm-gateway:8000/v1, your Windmill token as the API key), or make the"
                " API key in the space that already has it."
            )
        return found[0]

    def model_entry(self, model: dict, user: str | None = None, new_token=None) -> dict:
        """The runner's own entry for a gateway model, made if missing (see the top of this
        file). `model` is one entry of f/dhr/llm.models()["data"]: id (the gateway's key),
        label, model, context_window, concurrency. An entry whose context window or
        concurrency no longer match the gateway's is updated, and one whose token (made by
        this) is near its end gets a new one. new_token() -> (token, valid until); by default
        a Windmill token for the runner (make_gateway_token)."""
        user = user or runner() or "?"
        new_token = new_token or make_gateway_token
        label = f"{model['label']} ({user})"
        chat = [m for m in self.models() if m.get("type") == "KnowledgeQA"]
        if not self.shared:  # someone's own space: any chat model with that name will do
            found = [m for m in chat if model["id"] in (m.get("name"), m.get("display_name"))]
            if found:
                return found[0]
        mine = [m for m in chat if (m.get("display_name") or "").endswith(f"({user})")]
        found = [m for m in mine if m.get("name") == model["id"] and m.get("display_name") == label]
        if found:
            entry = found[0]
            params = entry.get("parameters") or {}
            if (params.get("context_window"), params.get("max_concurrency")) != (
                model["context_window"],
                model["concurrency"],
            ):
                entry = self._fit_entry(entry, model, label)
            until = VALID_UNTIL.search(entry.get("description") or "")
            if until and date.fromisoformat(until.group(1)) - date.today() < timedelta(
                days=RENEW_DAYS
            ):
                token, new_until = new_token()
                self.request("PUT", f"/models/{entry['id']}/credentials", json={"api_key": token})
                entry = self._fit_entry(
                    entry, model, label, self._describe(model) + _token_note(new_until)
                )
            return entry
        template = sorted(
            mine,  # an entry of theirs already carries their own token
            key=lambda m: (
                "llm-gateway" not in ((m.get("parameters") or {}).get("base_url") or ""),
                m.get("display_name") != model_label(user),
            ),
        )
        if template:
            copy = _data(
                self.request(
                    "POST", f"/models/{template[0]['id']}/copy", json={"display_name": label}
                )
            )
            return self._fit_entry(copy, model, label, template[0].get("description"))
        try:
            token, until = new_token()
        except Exception as error:  # noqa: BLE001 - say what to do instead
            raise WeKnoraError(
                f"Couldn't make a Windmill token for your WeKnora model entry ({error})."
                " Instead add one entry yourself in the team space: Settings > Models > Add"
                f" Model, chat, provider Custom (OpenAI-compatible), base URL {GATEWAY_URL},"
                " your own Windmill token as the API key, model name default, display name"
                f" {model_label(user)!r}. The pipeline copies it for each model."
            ) from error
        made = self.request(
            "POST",
            "/models",
            json={
                "name": model["id"],
                "display_name": label,
                "type": "KnowledgeQA",
                "source": "remote",
                "description": self._describe(model) + _token_note(until),
                "parameters": {
                    "base_url": GATEWAY_URL,
                    "api_key": token,
                    "provider": "generic",  # WeKnora's "Custom (OpenAI-compatible)"
                    "context_window": model["context_window"],
                    "max_concurrency": model["concurrency"],
                },
            },
        )
        return _data(made)

    @staticmethod
    def _describe(model: dict) -> str:
        return (
            f"{model['model']} through the NarraTrace LLM gateway, on your own Blythe account."
            " Made by f/dhr/O2/check_setup."
        )

    def _fit_entry(
        self, entry: dict, model: dict, label: str, description: str | None = None
    ) -> dict:
        """Names `entry` after the gateway model and sets its context window and background
        concurrency. Its API key (the person's token) is kept: WeKnora never changes a
        stored key through this call. The description is kept unless one is given."""
        params = dict(entry.get("parameters") or {})
        params["context_window"] = model["context_window"]
        params["max_concurrency"] = model["concurrency"]
        if isinstance(params.get("spec"), dict) and params["spec"].get("context_window"):
            params["spec"] = {**params["spec"], "context_window": model["context_window"]}
        description = description or entry.get("description") or self._describe(model)
        if not VALID_UNTIL.search(description) and "NarraTrace LLM gateway" not in description:
            description = self._describe(model)
        body = {
            "name": model["id"],
            "display_name": label,
            "description": description,
            "parameters": params,
            "source": entry.get("source") or "remote",
            "type": entry.get("type") or "KnowledgeQA",
        }
        return _data(self.request("PUT", f"/models/{entry['id']}", json=body)) or {
            **entry,
            **body,
        }

    # --- knowledge bases ----------------------------------------------------------------

    def create_knowledge_base(
        self,
        *,
        name: str,
        description: str,
        chat_model_id: str,
        granularity: str = "standard",
        extraction_instructions: str = "",
        content_instructions: str = "",
        chunking_config: dict | None = None,
        wiki_tuning: dict | None = None,
        extract_config: dict | None = None,
    ) -> dict:
        """A wiki knowledge base: wiki on; vector and keyword indexes off (no embedding model
        is needed, and the gateway has none); the knowledge graph off unless extract_config.
        chunking_config and wiki_tuning come from f/dhr/O2/kb_settings.py (checked there);
        left out, WeKnora's defaults. extract_config (f/dhr/O2/graph.py) turns the knowledge
        graph on as well."""
        body = {
            "name": name,
            "description": description,
            "type": "document",
            "summary_model_id": chat_model_id,
            "indexing_strategy": {
                "vector_enabled": False,
                "keyword_enabled": False,
                "wiki_enabled": True,
                "graph_enabled": bool(extract_config),
            },
            "wiki_config": {
                "synthesis_model_id": chat_model_id,
                "extraction_granularity": granularity,
                "extraction_instructions": extraction_instructions,
                "content_instructions": content_instructions,
                **(wiki_tuning or {}),
            },
        }
        if chunking_config:
            body["chunking_config"] = chunking_config
        if extract_config:
            body["extract_config"] = extract_config
        return _data(self.request("POST", "/knowledge-bases", json=body))

    def graph_database(self) -> str:
        """WeKnora's graph database: "Neo4j", or "Not Enabled" when the server has none."""
        return (
            _data(self.request("GET", "/system/info")).get("graph_database_engine") or "Not Enabled"
        )

    def knowledge_base(self, kb_id: str) -> dict:
        return _data(self.request("GET", f"/knowledge-bases/{kb_id}"))

    def delete_knowledge_base(self, kb_id: str) -> bool:
        """Deletes a knowledge base and everything in it: its files, chunks, wiki pages and
        knowledge graph (WeKnora stops its queued work at once and clears the rest in the
        background). False if it was already gone."""
        try:
            self.request("DELETE", f"/knowledge-bases/{kb_id}", retries=1)
        except WeKnoraError as error:
            if "answered 404" in str(error):
                return False
            raise
        return True

    def upload_markdown(self, kb_id: str, file_name: str, text: str) -> dict:
        """Uploads one document as a Markdown file. (The manual-text route caps documents at
        200,000 characters, and the longest reviews are nearly twice that.)"""
        files = {"file": (file_name, text.encode("utf-8"), "text/markdown")}
        return _data(
            self.request(
                "POST",
                f"/knowledge-bases/{kb_id}/knowledge/file",
                files=files,
                data={"fileName": file_name},
                retries=1,
            )
        )

    def documents(self, kb_id: str) -> list[dict]:
        """Every document in the knowledge base, with its parse_status."""
        found, page = [], 1
        while True:
            body = self.request(
                "GET",
                f"/knowledge-bases/{kb_id}/knowledge",
                params={
                    "page": page,
                    "page_size": 200,
                    "sort_by": "created_at",
                    "sort_order": "asc",
                },
            )
            batch = _data(body) or []
            found.extend(batch)
            total = body.get("total", len(found)) if isinstance(body, dict) else len(found)
            if not batch or len(found) >= total:
                return found
            page += 1

    # --- the wiki -----------------------------------------------------------------------

    def wiki_stats(self, kb_id: str) -> dict:
        return _data(self.request("GET", f"/knowledgebase/{kb_id}/wiki/stats"))

    def wiki_pages(self, kb_id: str) -> list[dict]:
        """Every wiki page, in full (content, links, sources)."""
        found, page = [], 1
        while True:
            body = _data(
                self.request(
                    "GET",
                    f"/knowledgebase/{kb_id}/wiki/pages",
                    params={
                        "page": page,
                        "page_size": 200,
                        "sort_by": "title",
                        "sort_order": "asc",
                    },
                )
            )
            batch = body.get("pages") or []
            found.extend(batch)
            if not batch or page >= (body.get("total_pages") or 1):
                return found
            page += 1

    # Folders (f/dhr/O2/folders.py keeps their names in English). WeKnora updates the paths
    # of the pages inside a folder when it's renamed or moved, and of a page when it's moved.

    def wiki_folders(self, kb_id: str, parent_id: str = "") -> list[dict]:
        """The folders directly inside parent_id ("" = the top level), empty ones included.
        (WeKnora leaves out empty folders unless asked for more than one page type.)"""
        body = _data(
            self.request(
                "GET",
                f"/knowledgebase/{kb_id}/wiki/folders",
                params={"parent_id": parent_id, "page_types": "entity,concept,summary,index"},
            )
        )
        return (body.get("folders") if isinstance(body, dict) else body) or []

    def create_wiki_folder(self, kb_id: str, name: str, parent_id: str = "") -> dict:
        return _data(
            self.request(
                "POST",
                f"/knowledgebase/{kb_id}/wiki/folders",
                json={"name": name, "parent_id": parent_id},
                retries=1,
            )
        )

    def update_wiki_folder(
        self, kb_id: str, folder_id: str, *, name: str = "", parent_id: str | None = None
    ) -> dict:
        """Renames a folder (name) and/or moves it under parent_id ("" = the top level)."""
        body: dict = {"name": name} if name else {}
        if parent_id is not None:
            body.update(parent_id=parent_id, move_parent=True)
        return _data(
            self.request(
                "PUT", f"/knowledgebase/{kb_id}/wiki/folders/{folder_id}", json=body, retries=1
            )
        )

    def delete_wiki_folder(self, kb_id: str, folder_id: str) -> None:
        """Deletes an empty folder."""
        self.request("DELETE", f"/knowledgebase/{kb_id}/wiki/folders/{folder_id}", retries=1)

    def move_wiki_page(self, kb_id: str, slug: str, folder_id: str) -> dict:
        return _data(
            self.request(
                "PUT",
                f"/knowledgebase/{kb_id}/wiki/move-page",
                json={"slug": slug, "folder_id": folder_id},
                retries=1,
            )
        )

    def wiki_issues(self, kb_id: str) -> list[dict]:
        body = _data(self.request("GET", f"/knowledgebase/{kb_id}/wiki/issues"))
        if isinstance(body, dict):
            return body.get("issues") or body.get("items") or []
        return body or []
