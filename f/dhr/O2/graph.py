"""O2 stages 1-3, shared: the knowledge graph WeKnora builds beside each wiki.

Rowan, 25 Nov 2026: turn on WeKnora's knowledge graph for every build. This is not the
wiki's page-link graph (the O2 app's Graph tab). For every chunk of every uploaded file,
the model lists the people, agencies, places and events in it with their details, and the
relationships between them, chosen from RELATION_TYPES. WeKnora keeps them in its graph
database (weknora-neo4j in compose.yaml), and shows them in the knowledge base's graph view.

WeKnora's prompt teaches the task by one worked example: EXAMPLE_TEXT and the nodes and
relations it should give (EXAMPLE_NODES, EXAMPLE_RELATIONS). The example is made up, in the
style of a DHR, and names nobody real. To change which relationships are kept, change
RELATION_TYPES and the example together: the prompt tells the model to use only the listed
types, so an example using others would contradict it.

It costs a model call per chunk (one per chunk_size characters, about 250 for a median
review at WeKnora's default 512), on top of the wiki's own calls, so bigger chunks make the
graph much cheaper (f/dhr/O2/kb_settings.py).
"""

from __future__ import annotations

# The relationships the model may record (WeKnora's "tags"). Written as a reader would say
# them, as WeKnora's own examples are.
RELATION_TYPES = [
    "Partner of",
    "Former partner of",
    "Family member of",
    "Lives with",
    "Abused",
    "Killed",
    "Disclosed abuse to",
    "Contacted",
    "Referred to",
    "Assessed",
    "Supported",
    "Works for",
    "Took place at",
    "Happened on",
]

EXAMPLE_TEXT = """\
In March 2017 Adult A told her GP at Riverside Surgery that her partner, Adult B, had \
pushed her down the stairs at their home in Northtown. The GP referred her to the local \
domestic abuse service, Safe Steps, whose IDVA assessed her as high risk using the \
DASH checklist. Adult B was later convicted of her murder in 2018.
"""

EXAMPLE_NODES = [
    {
        "name": "Adult A",
        "attributes": ["The victim", "Disclosed being pushed down the stairs in March 2017"],
    },
    {
        "name": "Adult B",
        "attributes": ["Partner of Adult A", "Convicted of Adult A's murder in 2018"],
    },
    {
        "name": "GP at Riverside Surgery",
        "attributes": ["Adult A's GP", "Referred her to Safe Steps"],
    },
    {"name": "Safe Steps", "attributes": ["Local domestic abuse service"]},
    {
        "name": "IDVA at Safe Steps",
        "attributes": ["Assessed Adult A as high risk using the DASH checklist"],
    },
    {
        "name": "Home in Northtown",
        "attributes": ["Where Adult A and Adult B lived", "Where the assault happened"],
    },
    {"name": "Disclosure to GP", "attributes": ["Adult A told her GP about the assault"]},
    {"name": "March 2017", "attributes": ["When Adult A told her GP"]},
]

EXAMPLE_RELATIONS = [
    {"node1": "Adult B", "node2": "Adult A", "type": "Partner of"},
    {"node1": "Adult A", "node2": "Adult B", "type": "Lives with"},
    {"node1": "Adult B", "node2": "Adult A", "type": "Abused"},
    {"node1": "Adult B", "node2": "Adult A", "type": "Killed"},
    {"node1": "Adult A", "node2": "GP at Riverside Surgery", "type": "Disclosed abuse to"},
    {"node1": "GP at Riverside Surgery", "node2": "Safe Steps", "type": "Referred to"},
    {"node1": "IDVA at Safe Steps", "node2": "Adult A", "type": "Assessed"},
    {"node1": "IDVA at Safe Steps", "node2": "Safe Steps", "type": "Works for"},
    {"node1": "Disclosure to GP", "node2": "March 2017", "type": "Happened on"},
    {"node1": "Disclosure to GP", "node2": "GP at Riverside Surgery", "type": "Took place at"},
]

INSTRUCTIONS = """\
These are Domestic Homicide Reviews (DHRs) from England and Wales. Keep the people \
(by the pseudonym or role the review uses, e.g. "Adult A", "the victim's son"), the \
agencies and professionals (e.g. "GP", "Police", "IDVA", "MARAC"), the places, and the \
dated events: contacts, disclosures, referrals, risk assessments, incidents and the \
homicide. Give each person one name: use the review's pseudonym rather than a role \
wherever the text links them. Record only what the text states; do not infer abuse, \
relationships or risk that it does not describe. Ignore the review's own procedure \
(panel members, terms of reference, report writing) unless it describes the case.
"""


def extract_config(instructions: str = "") -> dict:
    """WeKnora's extract_config for a knowledge base with the graph on."""
    text = instructions.strip() or INSTRUCTIONS
    if len(text.encode("utf-8")) > 4000:
        raise ValueError("graph_instructions are over WeKnora's 4,000-byte limit")
    return {
        "enabled": True,
        "text": EXAMPLE_TEXT,
        "tags": list(RELATION_TYPES),
        "nodes": EXAMPLE_NODES,
        "relations": EXAMPLE_RELATIONS,
        "custom_instructions": text,
    }
