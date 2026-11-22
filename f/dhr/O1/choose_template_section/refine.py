"""O1 stage 5, shared: give every section a mapping, with as few doubtful as possible.

Rowan, 20 Nov 2026. Only labels change: no section's text is touched.

The model's own answer to each section (the second question) is kept as it was
(template_choices.answer, probability, probabilities, model_template). refine() then gives
each section its final mapping, in a review's reading order:
  1. model: the model's own choice, if it gave it at least DOUBTFUL;
  2. combined: torn between two template sections that together it gave at least COMBINED_AT
     (an analysis that is also the lessons learnt, say): mapped to the likelier, with the
     other kept as also_*. Not doubtful: the section is plainly both;
  3. extra: a doubtful match, or a section answered "none of these" and inside no section
     the model matched, is asked a third question (extra_prompt in prompts.py): is it one of
     EXTRA_SECTIONS, parts many DHRs have that the templates don't name? If so (and, for a
     doubtful match, the model was surer of that than of its match), that is its mapping;
  4. parent: a "none of these" section inside a mapped section goes with that section;
  5. nearest: the model's likeliest after "none of these", if it was less than NEAREST_BELOW
     sure of "none" (always doubtful);
then, in reverse reading order (subsections first):
  6. subsections: a heading with under CONTAINER_CHARS of text of its own, whose own mapping
     is doubtful or missing, takes what its subsections mostly got (weighted by their text),
     or "Report part heading" if no mapping has most of it ("Section two", "Part 4"...);
then, in reading order again: a section that goes with its parent follows the parent's final
mapping, and
  7. other: whatever still has no mapping goes to "Other content", a catch-all that is always
     doubtful, so the few sections nothing fits are kept together and easy to review;
  8. appendix: anything under an Appendix or Annex heading goes to "Appendices", whatever it
     is about; what it is about (the mapping it would otherwise have) is kept as also_*. Except
     an action plan: a section mapped to the action plan stays there, even in an appendix
     ("Appendix 2: Action plan"; Rowan, 21 Nov 2026).
A section is doubtful only for its own mapping: the model under DOUBTFUL for its own choice, an
extra section under DOUBTFUL, the nearest fit and Other content always, a parent's or
subsections' mapping when that is. Refining starts from the model's answers every time, so it
can be repeated after the rules or the extra sections change.

The extra sections are one more template per guidance (kind EXTRA_KIND). placements() works out
where each sits among a template's own sections, from where reviews most often have it, so the
standardised view puts it there rather than at the end.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from .prompts import NONE, Template, TemplateSection

EXTRA_KIND = "extra sections"
EXTRA_APPENDIX = 100  # stored after the guidance's own templates (placements order them)
EXTRA_TITLE = "Extra sections (not in the guidance)"
NEAREST_BELOW = 0.8  # like the review fallback: only when the model wasn't sure of "none"
DOUBTFUL = 0.5
COMBINED_AT = 0.8  # two template sections that together have this much make one mapping
CONTAINER_CHARS = 200  # a heading with less text of its own than this is a container
METHODS = ("model", "combined", "extra", "parent", "nearest", "subsections", "other", "appendix")
APPENDIX = re.compile(r"^\W*(\d+[.:\s]*)?(appendix|appendices|annex|annexe|annexes)\b", re.I)
APPENDICES = "Appendices"
REPORT_PART = "Report part heading"
OTHER = "Other content"
RULE_ONLY = {APPENDICES, REPORT_PART, OTHER}  # given by the rules, never offered to the model
# (heading, what goes there, only for these guidances or None for all). Positions are the
# order here, after leaving out those not for the guidance: only ever add to the end.
EXTRA_SECTIONS: list[tuple[str, str, set[str] | None]] = [
    (
        "Front matter",
        "title page, contents, preface or foreword, acknowledgements, protective markings "
        "such as OFFICIAL-SENSITIVE, confidentiality notices, distribution and document control",
        None,
    ),
    (
        "About the review and how it was done",
        "what a DHR is and why this one was held; how it was commissioned, run and quality "
        "assured; its methodology, panel and timescales; the IMRs and parallel reviews",
        None,
    ),
    (
        "Glossary and definitions",
        "glossary, abbreviations, definitions of domestic abuse, coercive control and other "
        "terms, and the laws and procedures they come from (DASH, MARAC, the Care Act...)",
        None,
    ),
    (
        "Local and national context",
        "the community safety partnership and the area, local services, and national policy "
        "or research on issues in the case (older people, mental health, faith communities...)",
        None,
    ),
    (
        "Agency-by-agency detail",
        "profiles of the agencies involved; each agency's contact with the people, IMR "
        "summary, single-agency findings or recommendations",
        None,
    ),
    (
        "Family, friends and perpetrator",
        "tributes to the victim; family members' and friends' views and part in the review; "
        "contact with the perpetrator and their views",
        None,
    ),
    (
        "Equality and diversity",
        "protected characteristics and whether they affected what happened or the services",
        {"dardr-2026"},  # the 2016 overview report template has its own
    ),
    (
        "Publication, dissemination and media",
        "who receives the report, its publication, and media handling",
        {"dardr-2026"},  # the 2016 overview report template has Dissemination
    ),
    (
        # Was "References, appendices and correspondence": appendices have their own now.
        "References and correspondence",
        "bibliography and references, Home Office quality-assurance letters and the "
        "responses to them",
        None,
    ),
    (
        APPENDICES,
        "everything in the report's appendices or annexes, whatever it is about",
        None,
    ),
    (
        REPORT_PART,
        "a heading that only names a part of the report (Section two, Part 4, Overview "
        "report...), whose subsections are about different things",
        None,
    ),
    (
        OTHER,
        "content that fits none of the template's sections and none of the other extra sections",
        None,
    ),
]


def extra_template(guidance: str) -> Template:
    """The extra sections for one guidance, as a template."""
    template = Template(f"{guidance}/extra", EXTRA_APPENDIX, EXTRA_TITLE, EXTRA_KIND)
    for heading, text, only in EXTRA_SECTIONS:
        if only is None or guidance in only:
            position = len(template.sections) + 1
            template.sections.append(TemplateSection(template.template, position, heading, text))
    return template


def asked_extras(extras: Template) -> Template:
    """The extra sections the model is offered (not those only the rules give)."""
    asked = Template(extras.template, extras.appendix, extras.title, extras.kind)
    asked.sections = [s for s in extras.sections if s.heading not in RULE_ONLY]
    return asked


def store_extra_template(conn, guidance: str) -> Template:
    """Adds or updates the guidance's extra sections. Never removes one: choices may use it."""
    t = extra_template(guidance)
    conn.execute(
        "INSERT INTO corpus.templates (template, guidance, appendix, title, kind)"
        " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (template) DO UPDATE"
        " SET appendix = EXCLUDED.appendix, title = EXCLUDED.title, kind = EXCLUDED.kind",
        (t.template, guidance, t.appendix, t.title, t.kind),
    )
    for s in t.sections:
        conn.execute(
            "INSERT INTO corpus.template_sections (template, position, heading, printed,"
            " guidance, page) VALUES (%s, %s, %s, false, %s, NULL)"
            " ON CONFLICT (template, position) DO UPDATE"
            " SET heading = EXCLUDED.heading, guidance = EXCLUDED.guidance",
            (t.template, s.position, s.heading, s.guidance),
        )
    return t


def _position(extras: Template, heading: str) -> int:
    return next(s.position for s in extras.sections if s.heading == heading)


# ---------------------------------------------------------------- deciding


def _said_none(row: dict, document: dict) -> bool:
    return document["template"] is not None and row["answer"] == NONE


def _model(row: dict) -> tuple[str, int] | None:
    t = row.get("model_template", row.get("template"))
    p = row.get("model_template_position", row.get("template_position"))
    return (t, p) if t is not None else None


def _top_two(row: dict) -> list[tuple[float, str, int]]:
    options = []
    for key, p in (row.get("probabilities") or {}).items():
        if key != "none":
            template, position = key.rsplit("#", 1)
            options.append((p, template, int(position)))
    return sorted(options, reverse=True)[:2]


def combined(row: dict) -> tuple[str, int, float] | None:
    """The other template section, if the model's doubtful answer and its runner-up (both
    template sections) together have at least COMBINED_AT."""
    m = _model(row)
    if m is None or (row.get("probability") or 0) >= DOUBTFUL:
        return None
    top = _top_two(row)
    if len(top) < 2 or (top[0][1], top[0][2]) != m:
        return None
    total = top[0][0] + top[1][0]
    return (top[1][1], top[1][2], total) if total >= COMBINED_AT else None


def _doubtful_match(row: dict) -> bool:
    return _model(row) is not None and (row.get("probability") or 0) < DOUBTFUL


def to_ask(rows: list[dict], parents: dict, documents: dict) -> list[tuple[str, str]]:
    """The sections to ask the extra question, in a review with a template: those said "none
    of these" that sit inside no section the model matched, and doubtful matches that aren't
    a combination of two template sections. rows: the model's answers, in reading order within
    each review; parents: (doc, section) -> parent section id or None."""
    model = {(r["doc_id"], r["section_id"]): r for r in rows}
    asked = []
    for r in rows:
        document = documents[r["doc_id"]]
        if document["template"] is None:
            continue
        if _doubtful_match(r) and combined(r) is None:
            asked.append((r["doc_id"], r["section_id"]))
            continue
        if not _said_none(r, document):
            continue
        up, inside = parents.get((r["doc_id"], r["section_id"])), False
        while up is not None:
            if _model(model.get((r["doc_id"], up), {})) is not None:
                inside = True
                break
            up = parents.get((r["doc_id"], up))
        if not inside:
            asked.append((r["doc_id"], r["section_id"]))
    return asked


def nearest(row: dict) -> tuple[str, int, float] | None:
    """The template section the model rated likeliest after "none of these", if it was less
    than NEAREST_BELOW sure of "none"."""
    if (row.get("none_probability") or 0) >= NEAREST_BELOW:
        return None
    top = _top_two(row)
    if not top:
        return None
    p, template, position = top[0]
    return template, position, p


def in_appendix(rows: list[dict], parents: dict) -> set[tuple[str, str]]:
    """The sections whose heading, or a heading they sit under, starts Appendix or Annex."""
    headings = {(r["doc_id"], r["section_id"]): r.get("heading") or "" for r in rows}
    inside = set()
    for key in headings:
        up = key
        while up is not None:
            if APPENDIX.match(headings.get(up, "")):
                inside.add(key)
                break
            parent = parents.get(up)
            up = (key[0], parent) if parent is not None else None
    return inside


def _blank(r: dict, extra: dict) -> dict:
    return {
        "doc_id": r["doc_id"],
        "section_id": r["section_id"],
        "template": None,
        "template_position": None,
        "method": "model",
        "method_probability": None,
        "refined_doubtful": False,
        "also_template": None,
        "also_position": None,
        "extra_answer": extra.get("answer"),
        "extra_probability": extra.get("probability"),
        "extra_probabilities": extra.get("probabilities"),
        "extra_error": extra.get("error"),
    }


def _set(change: dict, method: str, mapping, probability, doubtful: bool, also=None) -> None:
    change["method"] = method
    change["template"], change["template_position"] = mapping if mapping else (None, None)
    change["method_probability"] = probability
    change["refined_doubtful"] = doubtful
    change["also_template"], change["also_position"] = also if also else (None, None)


def _first_pass(r: dict, document: dict, extra: dict, parent: dict | None) -> dict:
    change = _blank(r, extra)
    if document["template"] is None:
        return change  # its review has no template: nothing to map onto
    m, p = _model(r), r.get("probability")
    ex = (extra["template"], extra["template_position"]) if extra.get("template") else None
    ex_p = extra.get("probability") or 0
    if m is not None and (p or 0) >= DOUBTFUL:
        _set(change, "model", m, p, False)
    elif m is not None and (both := combined(r)):
        _set(change, "combined", m, both[2], False, (both[0], both[1]))
    elif m is not None:
        if ex and ex_p >= DOUBTFUL and ex_p > (p or 0):
            _set(change, "extra", ex, ex_p, False)
        else:
            _set(change, "model", m, p, True)
    elif r["answer"] == NONE:
        close = nearest(r)
        if parent and parent["template"] is not None:
            _set(
                change,
                "parent",
                (parent["template"], parent["template_position"]),
                parent["method_probability"],
                parent["refined_doubtful"],
            )
        elif ex:
            _set(change, "extra", ex, ex_p, ex_p < DOUBTFUL)
        elif close:
            _set(change, "nearest", close[:2], close[2], True)
    return change


def refine(
    rows: list[dict],
    parents: dict,
    documents: dict,
    extras: dict,
    guidance: str | None = None,
    action_plans: set[str] | None = None,
) -> list[dict]:
    """Every section's final row: template, template_position, method, method_probability,
    refined_doubtful, also_* and the extra question's answer (extras: (doc, section) ->
    interpreted answer). rows must be in reading order within each review, so a parent comes
    before its subsections; a row may carry heading and own_chars (its own text's length) for
    rules 6 and 8. guidance: whose extra sections (taken from the rows' keys if not given).
    action_plans: the action plan templates, whose sections stay put in an appendix."""
    guidance = guidance or _guidance(rows, documents)
    xt = extra_template(guidance) if guidance else None
    by_key: dict[tuple[str, str], dict] = {}
    order = []
    # 1-5, in reading order
    for r in rows:
        key = (r["doc_id"], r["section_id"])
        parent_id = parents.get(key)
        parent = by_key.get((r["doc_id"], parent_id)) if parent_id is not None else None
        by_key[key] = _first_pass(r, documents[r["doc_id"]], extras.get(key) or {}, parent)
        order.append(key)
    if xt is None:
        return [by_key[k] for k in order]
    report_part = (xt.template, _position(xt, REPORT_PART))
    other = (xt.template, _position(xt, OTHER))
    no_vote = {report_part, other}
    rows_by_key = {(r["doc_id"], r["section_id"]): r for r in rows}
    children: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for key in order:
        parent_id = parents.get(key)
        if parent_id is not None and (key[0], parent_id) in by_key:
            children[(key[0], parent_id)].append(key)
    # 6, subsections first; weights are each subsection's text, its own subsections' included
    weight: dict[tuple[str, str], int] = {}
    for key in reversed(order):
        own = rows_by_key[key].get("own_chars")
        weight[key] = (own if own is not None else CONTAINER_CHARS) + sum(
            weight[c] for c in children.get(key, [])
        )
        c = by_key[key]
        kids = children.get(key)
        if (
            not kids
            or documents[key[0]]["template"] is None
            or own is None
            or own >= CONTAINER_CHARS
            or (c["template"] is not None and not c["refined_doubtful"])
        ):
            continue
        votes: Counter = Counter()
        doubt: dict[tuple, bool] = {}
        for k in kids:
            kc = by_key[k]
            mapping = (kc["template"], kc["template_position"])
            if kc["template"] is None or kc["method"] == "parent" or mapping in no_vote:
                continue
            votes[mapping] += weight[k] + 1
            doubt[mapping] = doubt.get(mapping, True) and kc["refined_doubtful"]
        if not votes:
            continue
        mapping, n = votes.most_common(1)[0]
        share = n / sum(votes.values())
        if share >= DOUBTFUL:
            _set(c, "subsections", mapping, round(share, 4), doubt[mapping])
        else:
            _set(c, "subsections", report_part, round(share, 4), False)
    # parents' final mappings, then 7 and 8, in reading order
    for key in order:
        c = by_key[key]
        parent_id = parents.get(key)
        parent = by_key.get((key[0], parent_id)) if parent_id is not None else None
        said_none = _said_none(rows_by_key[key], documents[key[0]])
        if (
            parent
            and parent["template"] is not None
            and (c["method"] == "parent" or (said_none and c["template"] is None))
        ):
            _set(
                c,
                "parent",
                (parent["template"], parent["template_position"]),
                parent["method_probability"],
                parent["refined_doubtful"],
            )
        if c["template"] is None and said_none:
            _set(c, "other", other, None, True)
    appendix = (xt.template, _position(xt, APPENDICES))
    for key in in_appendix(rows, parents):
        c = by_key[key]
        if documents[key[0]]["template"] is None:
            continue
        if c["template"] in (action_plans or ()):
            continue  # an action plan stays the action plan, even in an appendix
        about = (c["template"], c["template_position"]) if c["template"] is not None else None
        _set(c, "appendix", appendix, None, False, about if about not in no_vote else None)
    return [by_key[k] for k in order]


def _guidance(rows: list[dict], documents: dict) -> str | None:
    for d in documents.values():
        if d.get("template"):
            return d["template"].split("/", 1)[0]
    return None


# ---------------------------------------------------------------- where extra sections go


def placements(
    changes: list[dict], documents: dict, extras_template: str, excluded: set[int]
) -> list[dict]:
    """Where each extra section sits among each document template's own sections: after the
    template section reviews most often have just before it (0: before the first). changes:
    every section's final row, in reading order within each review. excluded: extra
    positions placed by the view instead (Appendices: always last)."""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for c in changes:
        by_doc[c["doc_id"]].append(c)
    votes: dict[tuple[str, int], Counter] = defaultdict(Counter)
    ranks: dict[tuple[str, int], list[float]] = defaultdict(list)
    for doc, sections in by_doc.items():
        template = documents[doc]["template"]
        if template is None:
            continue
        seen, previous = set(), 0
        for i, c in enumerate(sections):
            if c["template"] == template:
                previous = c["template_position"]
            elif c["template"] == extras_template and c["template_position"] not in excluded:
                position = c["template_position"]
                if position in seen:
                    continue
                seen.add(position)
                votes[(template, position)][previous] += 1
                ranks[(template, position)].append(i / max(1, len(sections) - 1))
    result = []
    for (template, position), counter in votes.items():
        after, n = max(counter.items(), key=lambda kv: (kv[1], -kv[0]))
        r = ranks[(template, position)]
        result.append(
            {
                "template": template,
                "extra_template": extras_template,
                "extra_position": position,
                "after_position": after,
                "rank": round(sum(r) / len(r), 4),
                "n_reviews": n,
                "n_seen": sum(counter.values()),
            }
        )
    return sorted(result, key=lambda p: (p["template"], p["after_position"], p["rank"]))


# ---------------------------------------------------------------- the database

CHOICES_SQL = """
WITH own AS (
    SELECT doc_id, section_id, sum(length(text)) AS chars
    FROM corpus.blocks
    WHERE revision = %(revision)s AND section_id IS NOT NULL AND block_type <> 'SectionHeader'
    GROUP BY doc_id, section_id
)
SELECT c.doc_id, c.section_id, s.parent_id, s.heading, coalesce(own.chars, 0), c.answer,
       c.probability, c.none_probability, c.probabilities, c.model_template,
       c.model_template_position, c.extra_answer, c.extra_probability, c.extra_probabilities,
       c.extra_error
FROM corpus.template_choices AS c
JOIN corpus.sections AS s USING (revision, doc_id, section_id)
LEFT JOIN own USING (doc_id, section_id)
WHERE c.revision = %(revision)s AND c.guidance = %(guidance)s
ORDER BY c.doc_id, s.position
"""
DOCUMENTS_SQL = """
SELECT doc_id, template FROM corpus.document_choices
WHERE revision = %(revision)s AND guidance = %(guidance)s
"""


def _json(value) -> dict:
    return value if isinstance(value, dict) else json.loads(value or "{}")


def stored_extra(answer, probability, probabilities, error) -> dict | None:
    """An extra-question answer as stored, interpreted again (for refining without asking)."""
    if answer is None and error is None:
        return None
    probabilities = _json(probabilities) if probabilities is not None else {}
    template = position = None
    if answer not in (None, NONE):
        options = {k: p for k, p in probabilities.items() if k != "none"}
        if options:
            template, position = max(options, key=options.get).rsplit("#", 1)
            position = int(position)
    return {
        "template": template,
        "template_position": position,
        "answer": answer,
        "probability": probability,
        "probabilities": probabilities,
        "error": error,
    }


def read_choices(conn, revision: str, guidance: str):
    """The model's own answers, the sections' parents and each review's template. Each row
    also carries the extra question's stored answer (stored_extra) for refining again."""
    args = {"revision": revision, "guidance": guidance}
    rows, parents = [], {}
    for (
        doc,
        section,
        parent,
        heading,
        chars,
        answer,
        p,
        none,
        ps,
        template,
        position,
        x_answer,
        x_p,
        x_ps,
        x_error,
    ) in conn.execute(CHOICES_SQL, args):
        parents[(doc, section)] = parent
        rows.append(
            {
                "doc_id": doc,
                "section_id": section,
                "heading": heading,
                "own_chars": chars,
                "answer": answer,
                "probability": p,
                "none_probability": none,
                "probabilities": _json(ps),
                "model_template": template,
                "model_template_position": position,
                "stored_extra": stored_extra(x_answer, x_p, x_ps, x_error),
            }
        )
    documents = {
        doc: {"doc_id": doc, "template": t} for doc, t in conn.execute(DOCUMENTS_SQL, args)
    }
    return rows, parents, documents


UPDATE_SQL = """
UPDATE corpus.template_choices AS c
SET template = u.template, template_position = u.template_position, method = u.method,
    method_probability = u.method_probability, refined_doubtful = u.refined_doubtful,
    also_template = u.also_template, also_position = u.also_position,
    extra_answer = u.extra_answer, extra_probability = u.extra_probability,
    extra_probabilities = u.extra_probabilities, extra_error = u.extra_error
FROM refined AS u
WHERE c.revision = %(revision)s AND c.guidance = %(guidance)s
  AND c.doc_id = u.doc_id AND c.section_id = u.section_id
"""
COUNTS_SQL = """
SELECT count(*) FILTER (WHERE status = 'matched'),
       count(*) FILTER (WHERE status = 'unmatched'),
       count(*) FILTER (WHERE status = 'no answer'),
       count(*) FILTER (WHERE doubtful),
       count(*) FILTER (WHERE method = 'parent'),
       count(*) FILTER (WHERE method = 'extra'),
       count(*) FILTER (WHERE method = 'nearest'),
       count(*) FILTER (WHERE method = 'combined'),
       count(*) FILTER (WHERE method = 'subsections'),
       count(*) FILTER (WHERE method = 'appendix'),
       count(*) FILTER (WHERE method = 'other'),
       (SELECT count(*) FROM corpus.current_document_choices AS d
        WHERE d.revision = %(revision)s AND d.guidance = %(guidance)s AND d.doubtful),
       (SELECT count(*) FROM corpus.document_choices AS d
        WHERE d.revision = %(revision)s AND d.guidance = %(guidance)s
          AND d.method = 'sections')
FROM corpus.current_template_choices
WHERE revision = %(revision)s AND guidance = %(guidance)s
"""
COUNT_COLUMNS = (
    "n_matched",
    "n_unmatched",
    "n_no_answer",
    "n_doubtful",
    "n_parent",
    "n_extra",
    "n_nearest",
    "n_combined",
    "n_subsections",
    "n_appendix",
    "n_other",
    "n_reviews_doubtful",
    "n_reviews_by_sections",
)


def store_refinement(
    conn,
    revision: str,
    guidance: str,
    changes: list[dict],
    details: dict,
    places: list[dict] | None = None,
):
    """Writes the refined rows, where the extra sections go and the version's new counts, in
    one transaction."""
    args = {"revision": revision, "guidance": guidance}
    with conn.transaction():
        conn.execute(
            "CREATE TEMP TABLE refined (doc_id text, section_id text, template text,"
            " template_position integer, method text, method_probability real,"
            " refined_doubtful boolean, also_template text, also_position integer,"
            " extra_answer text, extra_probability real, extra_probabilities jsonb,"
            " extra_error text) ON COMMIT DROP"
        )
        with conn.cursor() as cursor:
            with cursor.copy("COPY refined FROM STDIN") as copy:
                for c in changes:
                    copy.write_row(
                        (
                            c["doc_id"],
                            c["section_id"],
                            c["template"],
                            c["template_position"],
                            c["method"],
                            c["method_probability"],
                            c["refined_doubtful"],
                            c.get("also_template"),
                            c.get("also_position"),
                            c["extra_answer"],
                            c["extra_probability"],
                            None
                            if c["extra_probabilities"] is None
                            else json.dumps(c["extra_probabilities"]),
                            c["extra_error"],
                        )
                    )
        conn.execute(UPDATE_SQL, args)
        conn.execute(
            "DELETE FROM corpus.extra_placements WHERE revision = %(revision)s"
            " AND guidance = %(guidance)s",
            args,
        )
        for p in places or []:
            conn.execute(
                "INSERT INTO corpus.extra_placements (revision, guidance, template,"
                " extra_template, extra_position, after_position, rank, n_reviews, n_seen)"
                " VALUES (%(revision)s, %(guidance)s, %(template)s, %(extra_template)s,"
                " %(extra_position)s, %(after_position)s, %(rank)s, %(n_reviews)s, %(n_seen)s)",
                {**args, **p},
            )
        counts = dict(zip(COUNT_COLUMNS, conn.execute(COUNTS_SQL, args).fetchone(), strict=True))
        conn.execute(
            "UPDATE corpus.choice_versions SET "
            + ", ".join(f"{c} = %({c})s" for c in COUNT_COLUMNS)
            + ", refined_at = now(), refine_details = %(details)s"
            " WHERE revision = %(revision)s AND guidance = %(guidance)s",
            {**counts, **args, "details": json.dumps(details)},
        )
    return counts
