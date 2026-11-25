"""O2 stages 1-4, shared: every knowledge-base setting a wiki build may change, and the ones
it may not.

Rowan, 25 Nov 2026: the pipeline must be able to change the chunking, and every other
knowledge-base setting that can change without breaking the build. All of them are checked
here before anything is made, and saved in wiki.instances.settings, so builds made with
different settings can be compared.

What can change (flow inputs, all optional; the defaults are WeKnora's own unless noted):

Each review is uploaded whole, as one file. Splitting reviews into parts (part_chars, 25 Nov
2026) was taken out again the same day (Rowan: not a good idea; see f/dhr/O2/reviews.py).
WeKnora v0.9.0's wiki builder reads only the first 32,768 characters of each file
(maxContentForWiki, internal/application/service/wiki_ingest.go) and drops the rest, so for
most DHRs the wiki is built from the opening part of the review; check_setup says how many
reviews that affects.

Chunking (chunk_strategy, chunk_size, chunk_overlap, chunk_separators). WeKnora splits every
file into chunks. The wiki's extraction step reads a file's chunks joined back together, so
these don't change what it reads; they set the pieces the citation step ties each page to
(wiki.pages.chunk_refs) and the passages quoted back to the model when it writes or updates
a page. Smaller chunks: tighter citations. Larger: more context per citation.
chunk_strategy: auto (WeKnora picks from the document's shape), heading (at Markdown
headings, then by size), heuristic, recursive (by separators, then by size) or legacy.

Extraction (granularity, extraction_instructions, content_instructions): checked in
create_wiki, as before (f/dhr/O2/instructions.py).

Knowledge graph (knowledge_graph, default on; graph_instructions): WeKnora's
entity-relationship graph, built beside the wiki (Rowan, 25 Nov 2026; f/dhr/O2/graph.py).
It needs the server's graph database (weknora-neo4j), which check_setup checks first.

Throughput (ingest_batch_size, ingest_map_parallel, ingest_reduce_parallel,
ingest_max_inflight): how many files one wiki batch takes (WeKnora default 5), how many
files it extracts at once (10) and pages it writes at once (10), and how many batches run at
once for this wiki (4). They change speed, not results. Every call still goes through the
WeKnora model entry's own "background concurrency limit" (8 in docs/weknora.md) and one GPU,
so raising them past that only queues calls; the caps below keep a typo from flooding it.

What can't, and why (fixed in WeKnora.create_knowledge_base):
- vector and keyword indexes: they need an embedding model, and the gateway has none.
- the summary and synthesis models: there is one model, chosen per person (check_setup).
- image, speech, question generation, auto-tagging and profile settings: the files are text
  only, and those only serve search, which is off.
- parent/child chunks and token_limit: parent/child only serves vector search; token_limit
  only re-expresses chunk_size in tokens.
- max_pages_per_ingest: WeKnora v0.9.0 stores it but never reads it.
"""

from __future__ import annotations

from .graph import INSTRUCTIONS as GRAPH_INSTRUCTIONS
from .graph import extract_config

# WeKnora's wiki builder reads this many characters of each file and drops the rest.
WEKNORA_WIKI_READ_LIMIT = 32_768

STRATEGIES = ("auto", "heading", "heuristic", "recursive", "legacy")
CHUNK_SIZE_RANGE = (100, 10_000)  # WeKnora's own limits
MAX_SEPARATORS = 20  # WeKnora's own limit

# name: (WeKnora's default, lowest, highest). 0 given = WeKnora's default.
THROUGHPUT = {
    "ingest_batch_size": (5, 1, 50),
    "ingest_map_parallel": (10, 1, 32),
    "ingest_reduce_parallel": (10, 1, 32),
    "ingest_max_inflight": (4, 1, 16),
}

DEFAULTS = {
    "chunk_strategy": "auto",
    "chunk_size": 512,
    "chunk_overlap": 80,
    "chunk_separators": None,  # None: WeKnora's own list
    **{name: 0 for name in THROUGHPUT},
    "knowledge_graph": True,
    "graph_instructions": "",
}


def _whole(label: str, value) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number, not {value!r}")
    return value


def check_settings(
    chunk_strategy: str = "auto",
    chunk_size: int = 512,
    chunk_overlap: int = 80,
    chunk_separators: list[str] | None = None,
    ingest_batch_size: int = 0,
    ingest_map_parallel: int = 0,
    ingest_reduce_parallel: int = 0,
    ingest_max_inflight: int = 0,
    knowledge_graph: bool = True,
    graph_instructions: str = "",
) -> dict:
    """Checks every value and returns them as saved in wiki.instances.settings["knowledge_base"].
    Raises ValueError naming the first bad one."""
    if chunk_strategy not in STRATEGIES:
        raise ValueError(
            f"chunk_strategy must be one of {', '.join(STRATEGIES)}, not {chunk_strategy!r}"
        )
    low, high = CHUNK_SIZE_RANGE
    if not low <= _whole("chunk_size", chunk_size) <= high:
        raise ValueError(f"chunk_size must be from {low:,} to {high:,} characters")
    # WeKnora quietly shrinks an overlap above half the chunk size; refuse it instead, so the
    # settings saved with a build are the ones it used.
    if not 0 <= _whole("chunk_overlap", chunk_overlap) <= chunk_size // 2:
        raise ValueError(f"chunk_overlap must be from 0 to half of chunk_size ({chunk_size // 2})")
    separators = None
    if chunk_separators:
        if not isinstance(chunk_separators, list) or not all(
            isinstance(s, str) and s for s in chunk_separators
        ):
            raise ValueError(
                'chunk_separators must be a list of non-empty strings, e.g. ["\\n\\n", "\\n"]'
            )
        if len(chunk_separators) > MAX_SEPARATORS:
            raise ValueError(f"chunk_separators may list at most {MAX_SEPARATORS}")
        separators = list(chunk_separators)
    values = {
        "ingest_batch_size": ingest_batch_size,
        "ingest_map_parallel": ingest_map_parallel,
        "ingest_reduce_parallel": ingest_reduce_parallel,
        "ingest_max_inflight": ingest_max_inflight,
    }
    for name, value in values.items():
        default, low, high = THROUGHPUT[name]
        if _whole(name, value) and not low <= value <= high:
            raise ValueError(
                f"{name} must be 0 (WeKnora's default, {default}) or from {low} to {high}"
            )
    if not isinstance(knowledge_graph, bool):
        raise ValueError(f"knowledge_graph must be true or false, not {knowledge_graph!r}")
    graph_text = None
    if knowledge_graph:
        extract_config(graph_instructions)  # checks the length
        graph_text = graph_instructions.strip() or GRAPH_INSTRUCTIONS
    return {
        "chunk_strategy": chunk_strategy,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "chunk_separators": separators,
        **values,
        "knowledge_graph": knowledge_graph,
        "graph_instructions": graph_text,
    }


def graph_config(settings: dict) -> dict | None:
    """WeKnora's extract_config, or None with the graph off (or for a build from before it)."""
    if not settings.get("knowledge_graph"):
        return None
    return extract_config(settings.get("graph_instructions") or "")


def chunking_config(settings: dict) -> dict:
    """WeKnora's chunking_config from checked settings."""
    config = {
        "strategy": settings["chunk_strategy"],
        "chunk_size": settings["chunk_size"],
        "chunk_overlap": settings["chunk_overlap"],
    }
    if settings.get("chunk_separators"):
        config["separators"] = settings["chunk_separators"]
    return config


def wiki_tuning(settings: dict) -> dict:
    """The wiki_config throughput fields that were set (WeKnora reads 0 or absent as its default)."""
    return {name: settings[name] for name in THROUGHPUT if settings.get(name)}
