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

Fix (25 Nov 2026, problem review "O2 knowledge graph split by review"): Problem: the graph
was one island per review, because WeKnora keeps each file's entities apart, and agencies
were named differently from review to review ("the police", "the Met", "Police"). Why: WeKnora
saves an entity under its name and its file (apoc.merge.node on name and kg), which no prompt
can change, and the prompt didn't say how to name agencies. Solution: the model now starts
each entity's details with its kind ("Kind: person", "Kind: agency", ...) and each agency's
with its type ("Type: Community Safety Partnership"), so the O2 app and the analysis can join
a named organisation across reviews, group organisations of one type under a node for that
type (Wiltshire and Bromley CSPs stay two), and never join people (f/dhr/O2/entities.py). It
names each agency in full, with its area, or by its type alone when the review gives no name,
so the same agency matches and different ones don't. Builds made before this have no kinds
or types; for those, f/dhr/O2/entities.py goes by the name.

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
        "attributes": [
            "Kind: person",
            "The victim",
            "Disclosed being pushed down the stairs in March 2017",
        ],
    },
    {
        "name": "Adult B",
        "attributes": [
            "Kind: person",
            "Partner of Adult A",
            "Convicted of Adult A's murder in 2018",
        ],
    },
    {
        "name": "GP at Riverside Surgery",
        "attributes": ["Kind: professional", "Adult A's GP", "Referred her to Safe Steps"],
    },
    {
        "name": "Safe Steps",
        "attributes": [
            "Kind: agency",
            "Type: Domestic abuse service",
            "Local domestic abuse service",
        ],
    },
    {
        "name": "IDVA at Safe Steps",
        "attributes": [
            "Kind: professional",
            "Assessed Adult A as high risk using the DASH checklist",
        ],
    },
    {
        "name": "Home in Northtown",
        "attributes": [
            "Kind: place",
            "Where Adult A and Adult B lived",
            "Where the assault happened",
        ],
    },
    {
        "name": "Disclosure to GP",
        "attributes": ["Kind: event", "Adult A told her GP about the assault"],
    },
    {"name": "March 2017", "attributes": ["Kind: date", "When Adult A told her GP"]},
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
homicide. Start each entity's details with its kind, exactly one of "Kind: person", \
"Kind: agency", "Kind: professional", "Kind: place", "Kind: event" or "Kind: date" (an \
agency is an organisation or service; a professional is a person in a role). For an \
agency, add its general type next, e.g. "Type: Police force", "Type: Community Safety \
Partnership", "Type: GP practice", "Type: Local authority", "Type: Domestic abuse \
service". Name each agency by its full official name, in full rather than abbreviated, \
with its area where the review gives it (e.g. "Wiltshire Police", "Bromley Community \
Safety Partnership", "Crown Prosecution Service"; not "the police" or "the CSP"), and an \
agency the review doesn't name by its type alone (e.g. "Police force", "GP practice"), so \
the same agency has the same name in every review and different ones never share a name. \
Never add a review's pseudonyms to an agency's name. Give each \
person one name: use the review's pseudonym rather than a role \
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
