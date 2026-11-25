"""O2 stage 4, shared: which knowledge-graph entities are the same thing in different reviews,
and what type of organisation each agency is.

Fix (25 Nov 2026, problem review "O2 knowledge graph split by review"):
Problem: WeKnora keeps each review's entities apart, so the Crown Prosecution Service in ten
reviews was ten separate entities, and the knowledge graph was a hundred unconnected islands,
one per review. Rowan wants the reviews joined up by default, everywhere the graph is shown or
analysed, with similar organisations grouped but not merged: Wiltshire Community Safety
Partnership and Bromley Community Safety Partnership are two organisations of one type.
Why not simply join every entity with the same name: DHRs name people by pseudonym or role,
and those repeat across reviews for different people ("Adult A", "the victim", "Mother", "the
GP", a first name like "Mary"); joining them would invent links between unrelated cases.
"The police" in two reviews may be two different forces, so an agency the review doesn't name
can't be joined either.
Solution: three rules, applied when the graph is read (WeKnora's data is never changed):
1. A named organisation is one entity across all the reviews that name it: a national body
   ("Crown Prosecution Service", "Home Office") or a named local one ("Wiltshire Police",
   "Bromley Community Safety Partnership"). Names are matched after tidying (case, spaces,
   a leading "the", outer punctuation, a bracketed abbreviation) and spelling out common
   abbreviations (CSP, CPS, NPS, ASC, CSC, MARAC...), so "Wiltshire CSP", "Wiltshire Community
   Safety Partnership" and "Wiltshire Community Safety Partnership (CSP)" match. NATIONAL lists
   national bodies that are always named, whatever words they use.
2. Each agency also links to a node for its type ("Community Safety Partnership", "Police
   force", "GP practice"...) by an "Is a" relationship: organisations of one type are grouped
   under it but stay separate.
3. An agency the review doesn't name, only types ("the police", "GP practice", "Crown Court",
   "Police station": names made only of GENERIC_WORDS), stays with its review and links to
   its type. People, professionals, homes, events and dates always stay
   with their review, and so do roles ("Chair of the CSP") and documents ("NPS IMR").
Which entities are agencies, and of what type: from builds made after 25 Nov ~13:00, the model
says so (f/dhr/O2/graph.py asks for "Kind: agency" and "Type: ..." details). For earlier
builds, which have no kinds, this goes by the name: organisation words make an agency, person
words make a person, and TYPE_PATTERNS gives the type.
"""

from __future__ import annotations

import re

_PUNCT = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_BRACKETS = re.compile(r"\([^)]*\)")  # "Crown Prosecution Service (CPS)"
_DETAIL = re.compile(r"^\s*(kind|type)\s*:\s*(.+?)\s*$", re.IGNORECASE)

KINDS = ("person", "agency", "professional", "place", "event", "date")
AGENCY_KINDS = {"agency", "organisation", "organization", "service"}

# Abbreviations spelt out before names are matched.
ABBREVIATIONS = {
    "csp": "community safety partnership",
    "cps": "crown prosecution service",
    "nps": "national probation service",
    "crc": "community rehabilitation company",
    "asc": "adult social care",
    "csc": "children's social care",
    "dwp": "department for work and pensions",
    "camhs": "child and adolescent mental health services",
    "cmht": "community mental health team",
    "mash": "multi-agency safeguarding hub",
    "marac": "multi-agency risk assessment conference",
    "mappa": "multi-agency public protection arrangements",
    "sab": "safeguarding adults board",
    "lscb": "local safeguarding children board",
    "ccg": "clinical commissioning group",
    "icb": "integrated care board",
    "yot": "youth offending team",
    "met": "metropolitan police service",
}

# Organisation types, most specific first: (pattern on the spelt-out name, type).
TYPE_PATTERNS = [
    (r"community safety partnership", "Community Safety Partnership"),
    (r"safeguarding adults? board", "Safeguarding Adults Board"),
    (
        r"safeguarding children|children'?s safeguarding partnership",
        "Safeguarding Children Partnership",
    ),
    (r"multi-agency risk assessment conference", "MARAC"),
    (r"multi-agency safeguarding hub", "Multi-Agency Safeguarding Hub"),
    (r"multi-agency public protection", "MAPPA"),
    (r"crown prosecution service", "Prosecution service"),
    (r"probation|community rehabilitation company", "Probation service"),
    (r"police|constabulary", "Police force"),
    (r"ambulance", "Ambulance service"),
    (r"fire (and rescue )?service", "Fire and rescue service"),
    (
        r"child and adolescent mental health|community mental health team|mental health",
        "Mental health service",
    ),
    (r"adult social care|adult services", "Adult social care"),
    (r"children'?s social care|children'?s services|children and famil", "Children's social care"),
    (
        r"surgery|gp practice|medical practice|medical centre|health centre|general practice",
        "GP practice",
    ),
    (r"hospital", "Hospital"),
    (
        r"nhs .*trust|foundation trust|health board|integrated care board|clinical commissioning",
        "NHS body",
    ),
    (
        r"(county|borough|city|district|metropolitan borough) council|local authority|council",
        "Local authority",
    ),
    (r"housing", "Housing provider"),
    (
        r"women'?s aid|refuge|domestic abuse service|domestic violence service|idva service"
        r"|victim support",
        "Domestic abuse or victim support service",
    ),
    (r"drug|alcohol|substance", "Drug and alcohol service"),
    (r"youth offending", "Youth offending service"),
    (r"prison|young offender institution", "Prison"),
    (r"court", "Court"),
    (r"school|college|academy|university", "School or college"),
    (r"department for work and pensions|jobcentre", "Department for Work and Pensions"),
    (r"home office", "Government department"),
]
_TYPES = [(re.compile(rf"\b(?:{p})"), t) for p, t in TYPE_PATTERNS]

ORGANISATION_WORDS = {
    "police", "constabulary", "council", "trust", "nhs", "hospital", "surgery", "practice",
    "service", "services", "probation", "court", "prison", "agency", "partnership", "board",
    "department", "unit", "team", "centre", "center", "clinic", "school", "college",
    "university", "refuge", "charity", "association", "authority", "ambulance", "housing",
    "office", "foundation", "ltd", "limited", "plc", "ministry", "government", "helpline",
    "hub", "conference", "arrangements", "company", "group", "project",
}  # fmt: skip
ORGANISATION_PHRASES = {
    "adult social care", "children's social care", "social care", "children's services",
    "adult services", "victim support", "women's aid", "community safety partnership",
    "multi-agency", "mental health", "crown prosecution service", "home office",
    "department for work and pensions", "youth offending", "gp practice", "gp surgery",
}  # fmt: skip
PERSON_WORDS = {
    "son", "daughter", "mother", "father", "mum", "dad", "brother", "sister", "partner",
    "ex-partner", "husband", "wife", "boyfriend", "girlfriend", "grandmother", "grandfather",
    "aunt", "uncle", "cousin", "friend", "neighbour", "neighbor", "colleague", "relative",
    "gp", "doctor", "nurse", "officer", "worker", "manager", "practitioner", "consultant",
    "idva", "isva", "chair", "author", "psychiatrist", "psychologist", "midwife", "visitor",
    "pc", "ds", "dc", "sergeant", "inspector", "detective", "constable", "mr", "mrs", "ms",
    "dr", "family",
}  # fmt: skip
HOME_WORDS = {"home", "house", "flat", "address", "property", "bedroom", "kitchen", "garden"}
# Documents, not organisations: "NPS IMR", "Multi-agency Statutory Guidance".
DOCUMENT_WORDS = {
    "imr", "report", "guidance", "policy", "strategy", "plan", "statement", "letter",
    "minutes", "chronology", "recommendation", "recommendations", "action",
}  # fmt: skip
# A role in an organisation is a person: "Chair of the CSP", "Manager at the refuge".
_ROLE_OF = re.compile(r"^([a-z'\-]+) (of|at|for|from|in) ")
ORG_POSSESSIVES = re.compile(r"\b(children|women|men|people|victims|survivors)'s?\b")
PSEUDONYM = re.compile(
    r"\b(adult|child|victim|perpetrator|subject|person|male|female)( [a-z0-9])?\b"
)


# National bodies: one organisation wherever a review names them.
NATIONAL = {
    "crown prosecution service", "national probation service", "home office",
    "nhs england", "nhs digital", "department for work and pensions", "victim support",
    "women's aid", "refuge", "samaritans", "ministry of justice",
    "his majesty's prison and probation service", "her majesty's prison and probation service",
    "cafcass", "safelives", "respect", "metropolitan police service",
    "national crime agency", "care quality commission", "independent office for police conduct",
}  # fmt: skip
# Words that say what kind of organisation something is, not which one. A name made only of
# these ("Police station", "Community mental health team", "Crown Court") names no particular
# organisation; one with any other word (a place, a brand: "Wiltshire Police") does.
GENERIC_WORDS = ORGANISATION_WORDS | {
    "local", "station", "triage", "meeting", "meetings", "referral", "imr", "report", "panel",
    "staff", "officer", "officers", "worker", "workers", "multi-agency", "safeguarding",
    "social", "care", "adult", "adults", "children", "children's", "child", "mental", "health",
    "community", "domestic", "abuse", "violence", "safety", "risk", "assessment", "clinical",
    "commissioning", "primary", "secondary", "general", "emergency", "crisis", "support",
    "advice", "advisory", "family", "families", "specialist", "integrated", "public",
    "protection", "youth", "offending", "victims", "victim", "drug", "alcohol", "substance",
    "misuse", "homeless", "homelessness", "the", "of", "and", "for", "in", "at", "on", "a",
    "an", "to", "&", "county", "borough", "city", "district", "metropolitan", "force", "fire",
    "rescue", "hospitals", "medical", "gp", "gps", "courts", "magistrates", "crown", "nhs",
    "nursing", "acute", "accident", "a&e", "out-of-hours", "out", "hours", "ward", "wards",
    "outreach", "street", "access", "duty", "intake", "front", "door", "contact", "centre's",
    "rehabilitation", "recovery", "treatment", "commissioners", "partnerships", "boards",
    "agencies", "organisation", "organisations", "provider", "providers", "trusts",
    "councils", "authorities", "departments", "units", "teams", "statutory", "voluntary",
    "sector", "third", "independent", "domestic-abuse", "idva", "isva", "advocacy",
    "professional", "professionals", "practitioners", "clinicians",
}  # fmt: skip


def tidy_name(name: str) -> str:
    """The form names are matched on: lower case, single spaces, straight apostrophes, no
    leading "the", no punctuation at either end."""
    text = _BRACKETS.sub(" ", (name or "").replace("’", "'"))
    text = " ".join(text.split()).lower()
    text = _PUNCT.sub("", text)
    if text.startswith("the "):
        text = text[4:]
    return text


def spell_out(tidy: str) -> str:
    """A tidied name with common abbreviations written in full."""
    return " ".join(ABBREVIATIONS.get(w, w) for w in tidy.split(" "))


def details(attributes) -> dict[str, str]:
    """The "Kind: ..." and "Type: ..." details the model gave, with lower-cased keys."""
    found: dict[str, str] = {}
    for attribute in attributes or []:
        m = _DETAIL.match(str(attribute))
        if m and m.group(1).lower() not in found:
            found[m.group(1).lower()] = m.group(2).strip()
    return found


def kind_of(attributes) -> str | None:
    kind = details(attributes).get("kind")
    return kind.lower() if kind else None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z'\-]*", text))


def _has(text: str, phrases: set[str]) -> bool:
    words = _words(text)
    return any((p in words) if " " not in p else (p in text) for p in phrases)


def _person_like(text: str) -> bool:
    """A person's name or role: a possessive ("Mary's GP"), a pseudonym such as "Adult A"
    or "Child 2", or a family word or professional role."""
    plain = ORG_POSSESSIVES.sub(lambda m: m.group(1), text)  # "children's services" is not
    if "'s" in plain or PSEUDONYM.search(plain):
        return True
    return any(w in PERSON_WORDS or w.rstrip("s") in PERSON_WORDS for w in _words(plain))


def type_from(text: str) -> str | None:
    """The organisation type a (tidied, spelt-out) name or type text matches, or None."""
    for pattern, name in _TYPES:
        if pattern.search(text):
            return name
    return None


def is_agency(name: str, attributes=None) -> bool:
    kind = kind_of(attributes)
    if kind is not None:
        return kind in AGENCY_KINDS
    tidy = spell_out(tidy_name(name))
    if not tidy:
        return False
    if _words(tidy) & DOCUMENT_WORDS:
        return False
    role = _ROLE_OF.match(tidy)
    if role and role.group(1) in PERSON_WORDS:
        return False
    if _has(tidy, ORGANISATION_PHRASES):  # before home words: "Home Office"
        return True
    if _has(tidy, HOME_WORDS) or _person_like(tidy):
        return False
    return _has(tidy, ORGANISATION_WORDS)


def agency_type(name: str, attributes=None) -> str | None:
    """The type an agency belongs to ("Community Safety Partnership"), or None."""
    given = details(attributes).get("type")
    if given:
        tidy_given = spell_out(tidy_name(given))
        return type_from(tidy_given) or tidy_given[:1].upper() + tidy_given[1:]
    return type_from(spell_out(tidy_name(name)))


def is_unnamed(name: str, type_name: str | None = None) -> bool:
    """An agency named only by what kind it is ("the police", "GP practice", "Police
    station", "Crown Court"), not which one: it can't be joined with another review's."""
    tidy = spell_out(tidy_name(name))
    if tidy in NATIONAL:
        return False
    words = [w.rstrip("'") for w in re.split(r"[\s/]+", tidy) if w]
    return all(w in GENERIC_WORDS or w.rstrip("s") in GENERIC_WORDS for w in words)


def joined_key(name: str, attributes=None) -> str | None:
    """The key an entity is joined on across reviews (a named organisation), or None."""
    tidy = tidy_name(name)
    if not tidy or not is_agency(name, attributes):
        return None
    if is_unnamed(name):
        return None
    return spell_out(tidy)


def entity_key(doc_id: str, name: str, attributes=None, join_reviews: bool = True) -> str:
    """One entity's key: "*␟<name>" when joined across reviews, "<review>␟<name>" otherwise."""
    if join_reviews:
        joined = joined_key(name, attributes)
        if joined:
            return f"*␟{joined}"
    return f"{doc_id}␟{tidy_name(name)}"


def type_key(type_name: str) -> str:
    return f"type␟{type_name.lower()}"


def describe(doc_id: str, name: str, attributes=None, join_reviews: bool = True):
    """(entity key, type name or None) for one entity as WeKnora stored it. The type is given
    for agencies only, and only when join_reviews is on (type nodes link reviews too)."""
    key = entity_key(doc_id, name, attributes, join_reviews)
    if join_reviews and is_agency(name, attributes):
        return key, agency_type(name, attributes)
    return key, None


IS_A = "Is a"


def assemble(node_rows, relation_rows, join_reviews: bool = True) -> dict:
    """The graph as shown and analysed, from WeKnora's per-review rows.

    node_rows: (doc_id, name, attributes[, n_chunks]); relation_rows: (doc_id, source,
    target, type). Returns {"nodes": {key: {name, docs, attributes, n_chunks, is_type}},
    "relations": {(source key, target key, type): count}}. With join_reviews, named
    organisations are joined across reviews and every agency gets an "Is a" link to a node
    for its type (is_type true); without it, every entity stays with its review and there
    are no type nodes. The name shown for a joined entity is its most used spelling."""
    from collections import Counter, defaultdict

    spellings: dict[str, Counter] = defaultdict(Counter)
    nodes: dict[str, dict] = {}
    key_of: dict[tuple[str, str], str] = {}
    relations: Counter = Counter()
    for doc_id, name, attributes, *rest in node_rows:
        if not tidy_name(name):
            continue
        key, type_name = describe(doc_id, name, attributes, join_reviews)
        key_of[(doc_id, name)] = key
        spellings[key][name.strip()] += 1
        node = nodes.setdefault(
            key, {"docs": set(), "attributes": [], "n_chunks": 0, "is_type": False}
        )
        node["docs"].add(doc_id)
        node["n_chunks"] += int(rest[0] or 0) if rest else 0
        for a in attributes or []:
            if a not in node["attributes"] and len(node["attributes"]) < 20:
                node["attributes"].append(a)
        if type_name:
            tkey = type_key(type_name)
            tnode = nodes.setdefault(
                tkey,
                {
                    "docs": set(),
                    "attributes": [],
                    "n_chunks": 0,
                    "is_type": True,
                    "name": type_name,
                },
            )
            tnode["docs"].add(doc_id)
            relations[(key, tkey, IS_A)] = 1
    for doc_id, source, target, rel_type in relation_rows:
        a, b = key_of.get((doc_id, source)), key_of.get((doc_id, target))
        if a and b and a != b:
            relations[(a, b, rel_type)] += 1
    for key, node in nodes.items():
        node["docs"] = sorted(node["docs"])
        if not node["is_type"]:
            node["name"] = spellings[key].most_common(1)[0][0]
    return {"nodes": nodes, "relations": dict(relations)}
