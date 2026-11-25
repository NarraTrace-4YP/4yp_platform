# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#     "psycopg[binary]==3.3.9",
#     "mlflow-skinny==3.18.0",
#     "wmill==1.875.0",
# ]
# ///
"""O1 stage 2: link case details.

Read the Home Office search form, query each filter, and collect every results
page. A review's metadata consists of the filters that returned it; an absent
filter does not distinguish a negative answer from missing information.

Store a complete library snapshot in the ``evaluation`` schema, which is kept
separate from the corpus available to the wiki agent. Dataset links use review
source URLs. One snapshot covers both datasets. Requests are rate limited and
transient failures are retried; partial collections are not stored as complete.
Use ``dry_run`` to collect and report matches without writing a snapshot.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookiejar import CookieJar

from ...db import apply_migrations, connect
from ...settings import ProgressBar, use_mlflow
from ...version import GIT_COMMIT

BASE_URL = "https://homicide-review.homeoffice.gov.uk/"
USER_AGENT = (
    "NarraTrace-4YP case-metadata collector (University of Warwick student project; "
    "+https://github.com/NarraTrace-4YP/4yp_platform)"
)
PAGE_SIZE = 100  # the largest page the site offers

# Filters not searched, because every review's listing already shows the answer.
SKIP_FIELDS = {
    "internal.upload-date[]",
    "Basic Case Details.Date of death - year[]",
    "Basic Case Details.Name of Community Safety Partnership[]",
}
# Searched first, to check the site really filters before spending half an hour on it.
CHECK_FIRST = ("Victim-Specific Information.Victims.Sex of victim[]", "Keywords[]")

RESULT_PREFIX = "DHR result "
DOWNLOAD_ID = re.compile(r"/download/([0-9A-Za-z]+)")
# Most searches say "Showing results 1 to 100 of 164"; theme searches say "Showing 80 results".
RESULTS_COUNT = re.compile(r"Showing results\s+([\d,]+)\s+to\s+([\d,]+)\s+of\s+([\d,]+)")
RESULTS_TOTAL = re.compile(r"Showing\s+([\d,]+)\s+results?\b")
CARD_DETAILS = {
    "CSP": "csp",
    "CSP Region": "csp_region",
    "Uploaded on": "uploaded",
    "Date of death": "date_of_death",
}


class SiteChanged(RuntimeError):
    """The site didn't behave as expected, so its answers can't be trusted."""


# Reading html.


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    parent: Element | None = None
    children: list[Element | str] = field(default_factory=list)

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def text(self) -> str:
        """All the text inside, with runs of whitespace collapsed to one space."""
        parts = []

        def walk(element: Element) -> None:
            for child in element.children:
                if isinstance(child, str):
                    parts.append(child)
                else:
                    walk(child)

        walk(self)
        return " ".join("".join(parts).split())

    def iter(self, tag: str | None = None) -> Iterator[Element]:
        """Every element inside this one (not itself), in page order."""
        for child in self.children:
            if isinstance(child, Element):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.iter(tag)

    def ancestors(self) -> Iterator[Element]:
        element = self.parent
        while element is not None:
            yield element
            element = element.parent


# Tags that never have a closing tag.
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "wbr"}
# Tags a browser closes by itself when the same tag opens again (<option>a<option>b).
SELF_CLOSING_SIBLINGS = {"option", "li", "p"}


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {})
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        if tag in SELF_CLOSING_SIBLINGS and self.current.tag == tag:
            self.current = self.current.parent or self.root
        element = Element(tag, {k: v or "" for k, v in attrs}, parent=self.current)
        self.current.children.append(element)
        if tag not in VOID_TAGS:
            self.current = element

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(
            Element(tag, {k: v or "" for k, v in attrs}, parent=self.current)
        )

    def handle_endtag(self, tag):
        # Close the nearest open element with this tag; ignore stray closing tags.
        element = self.current
        while element is not None and element.tag != tag:
            element = element.parent
        if element is not None and element.parent is not None:
            self.current = element.parent

    def handle_data(self, data):
        self.current.children.append(data)


def parse_html(html: str) -> Element:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# The search form.


@dataclass(frozen=True)
class FilterOption:
    field: str  # the form field, e.g. "Victim-Specific Information.Victims.Sex of victim[]"
    value: str  # what the form sends, e.g. "Female" or "16,19"
    label: str  # what the page shows next to it, e.g. "Female" or "16 - 19"
    group: str  # where it sits on the page, e.g. "About the victim / Sex"


@dataclass
class SearchForm:
    # Every field the form submits, in page order, so a search can be sent exactly as a
    # browser sends it: ("checkbox", name, value), ("select", name, default value) or
    # ("hidden", name, value).
    controls: list[tuple[str, str, str]]
    options: list[FilterOption]  # every checkbox and drop-down choice, in page order


def _find_search_form(page: Element) -> Element | None:
    for form in page.iter("form"):
        if form.attrs.get("method", "get").lower() == "get":
            return form
    return None


def filter_group_label(element: Element, page_label: str = "") -> str:
    """Find the nearest labelled group containing a search filter."""
    parts = [page_label] if page_label else []
    for ancestor in element.ancestors():
        if ancestor.tag == "fieldset":
            legend = next(
                (c for c in ancestor.children if isinstance(c, Element) and c.tag == "legend"),
                None,
            )
            if legend is not None and legend.text():
                parts.append(legend.text())
        elif "govuk-accordion__section" in ancestor.classes():
            heading = next(
                (e for e in ancestor.iter() if e.attrs.get("id", "").endswith("--heading")), None
            )
            if heading is not None and heading.text():
                parts.append(heading.text())
    return " / ".join(reversed(parts))


def parse_form(html: str) -> SearchForm:
    page = parse_html(html)
    form = _find_search_form(page)
    if form is None:
        raise SiteChanged("couldn't find the search form on the library's front page")
    labels_for = {e.attrs["for"]: e.text() for e in page.iter("label") if e.attrs.get("for")}
    texts_by_id = {e.attrs["id"]: e.text() for e in page.iter() if e.attrs.get("id")}

    controls: list[tuple[str, str, str]] = []
    options: list[FilterOption] = []
    for element in form.iter():
        name = element.attrs.get("name")
        if not name:
            continue
        if element.tag == "input" and element.attrs.get("type") == "checkbox":
            value = element.attrs.get("value", "on")
            controls.append(("checkbox", name, value))
            label = labels_for.get(element.attrs.get("id", ""), "") or value
            options.append(FilterOption(name, value, label, filter_group_label(element)))
        elif element.tag == "input" and element.attrs.get("type") == "hidden":
            controls.append(("hidden", name, element.attrs.get("value", "")))
        elif element.tag == "select":
            choices = [
                (o.attrs.get("value", o.text()), o.text(), "selected" in o.attrs)
                for o in element.iter("option")
            ]
            default = next(
                (v for v, _, chosen in choices if chosen), choices[0][0] if choices else ""
            )
            controls.append(("select", name, default))
            label_id = element.attrs.get("aria-labelledby", "")
            heading = texts_by_id.get(label_id) or labels_for.get(element.attrs.get("id", ""), "")
            group = filter_group_label(element, heading)
            options.extend(FilterOption(name, v, text, group) for v, text, _ in choices if v)
    if not options:
        raise SiteChanged("the search form has no filters on it")
    return SearchForm(controls, options)


def search_url(
    form: SearchForm, option: FilterOption | None, page: int, base_url: str = BASE_URL
) -> str:
    """The address a browser asks for when this one filter is ticked (None: no filter)."""
    params = []
    for kind, name, value in form.controls:
        if kind == "checkbox":
            if option is not None and (name, value) == (option.field, option.value):
                params.append((name, value))
        elif kind == "select":
            params.append((name, option.value if option and option.field == name else value))
        elif name == "pagination[pageSize]":
            params.append((name, str(PAGE_SIZE)))
        else:
            params.append((name, value))
    if not any(name == "pagination[pageSize]" for _, name, _ in form.controls):
        params.append(("pagination[pageSize]", str(PAGE_SIZE)))
    if page:  # the site counts pages from 0, and leaves the number off the first page
        params.append(("pagination[pageNumber]", str(page)))
    return base_url + "?" + urllib.parse.urlencode(params)


# The results.


@dataclass(frozen=True)
class Review:
    library_id: str  # the id in its download link, e.g. 6af5ab5f97cddc1cd42a5658
    full_name: str  # e.g. "Debbie - Havering - 2023/a - DHR"
    title: str  # e.g. "Debbie"
    csp: str | None
    csp_region: str | None
    uploaded: str | None  # as the site shows it, e.g. "November 2026"
    date_of_death: str | None  # likewise, e.g. "May 2023"


@dataclass
class ResultsPage:
    reviews: list[Review]
    # "Showing results <first> to <last> of <total>"; all 0 when nothing matched. Pages that
    # only say "Showing <total> results" (theme searches) have first and last as None.
    first: int | None
    last: int | None
    total: int


def parse_result_count(text: str) -> int:
    """Read a result count while ignoring surrounding text and separators."""
    return int(text.replace(",", ""))


def parse_review_card(card: Element) -> Review:
    """Extract a library review and its download link from one results card."""
    full_name = card.attrs["aria-label"][len(RESULT_PREFIX) :].strip()
    library_id = None
    for link in card.iter("a"):
        match = DOWNLOAD_ID.search(link.attrs.get("href", ""))
        if match:
            library_id = match.group(1)
            break
    if library_id is None:
        raise SiteChanged(f"the result {full_name!r} has no download link")

    title = full_name
    heading = next(card.iter("h3"), None)
    if heading is not None:
        caption = " ".join(
            e.text() for e in heading.iter("span") if "govuk-caption-m" in e.classes()
        )
        title = heading.text().removeprefix(caption).strip() or full_name

    details: dict[str, str | None] = dict.fromkeys(CARD_DETAILS.values())
    for value in card.iter("span"):
        if "dhrr-results-card--detail-value" not in value.classes() or value.parent is None:
            continue
        caption = next(
            (e.text() for e in value.parent.iter("span") if "govuk-caption-m" in e.classes()), ""
        )
        if caption in CARD_DETAILS:
            details[CARD_DETAILS[caption]] = value.text() or None
    return Review(library_id=library_id, full_name=full_name, title=title, **details)


def parse_results(html: str) -> ResultsPage:
    page = parse_html(html)
    if _find_search_form(page) is None:
        # Every real results page has the filters beside it; anything else is an error page.
        raise SiteChanged("got a page without the search form, so not a results page")
    cards = [
        s for s in page.iter("section") if s.attrs.get("aria-label", "").startswith(RESULT_PREFIX)
    ]
    reviews = [parse_review_card(card) for card in cards]
    text = page.text()
    first = last = None
    if match := RESULTS_COUNT.search(text):
        first, last, total = (parse_result_count(g) for g in match.groups())
        if reviews and last - first + 1 != len(reviews):
            raise SiteChanged(f"the page says results {first} to {last} but shows {len(reviews)}")
    elif match := RESULTS_TOTAL.search(text):
        total = parse_result_count(match.group(1))
        if len(reviews) > total:
            raise SiteChanged(f"the page says {total} results but shows {len(reviews)}")
    elif not reviews:
        first = last = total = 0  # nothing matched, so there's no count to show
    else:
        raise SiteChanged("the page has results but doesn't say how many ('Showing ...')")
    return ResultsPage(reviews, first, last, total)


# Asking the site.


class Fetcher:
    """Gets pages politely: one request per `delay` seconds, retrying short outages."""

    def __init__(self, delay: float = 1.0, timeout: float = 60, waits=(10, 30, 90)) -> None:
        self.delay = delay
        self.timeout = timeout
        self.waits = waits
        self.requests = 0
        self._last = 0.0
        # Keeps any cookies the site sets, as a browser would.
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def get(self, url: str) -> str:
        for attempt, wait in enumerate([*self.waits, None], start=1):
            pause = self._last + self.delay - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self._last = time.monotonic()
            self.requests += 1
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    charset = response.headers.get_content_charset() or "utf-8"
                    return response.read().decode(charset, errors="replace")
            except urllib.error.HTTPError as error:
                if error.code != 429 and error.code < 500:
                    raise RuntimeError(f"the library answered {error.code} for {url}") from error
                problem = f"HTTP {error.code}"
                retry_after = error.headers.get("Retry-After", "")
                if wait is not None and retry_after.isdigit():
                    wait = max(wait, int(retry_after))
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                problem = str(getattr(error, "reason", error))
            if wait is None:
                raise RuntimeError(f"gave up on {url} after {attempt} tries ({problem})")
            print(f"        {problem}; trying again in {wait}s", flush=True)
            time.sleep(wait)
        raise AssertionError("unreachable")


def search(
    get: Callable[[str], str],
    form: SearchForm,
    option: FilterOption | None,
    base_url: str = BASE_URL,
) -> tuple[int, list[Review]]:
    """Every review the site returns for one filter (None: the whole library), all pages."""
    found: dict[str, Review] = {}
    total = None
    page = 0
    while True:
        result = parse_results(get(search_url(form, option, page, base_url)))
        what = f"'{option.group} / {option.label}'" if option else "the full list"
        if total is None:
            total = result.total
        elif result.total != total:
            raise SiteChanged(f"{what}: page {page + 1} says {result.total} results, not {total}")
        if result.reviews and result.first is not None and result.first != len(found) + 1:
            raise SiteChanged(
                f"{what}: page {page + 1} starts at result {result.first}, expected {len(found) + 1}"
            )
        new = [r for r in result.reviews if r.library_id not in found]
        if result.reviews and not new:
            raise SiteChanged(f"{what}: page {page + 1} repeated reviews already seen")
        found.update((r.library_id, r) for r in new)
        if len(found) >= total or not result.reviews:
            break
        page += 1
    if len(found) != total:
        raise SiteChanged(f"{what}: the site says {total} results but gave {len(found)}")
    return total, list(found.values())


@dataclass
class Collection:
    total: int  # how many reviews the library says it has
    reviews: dict[str, Review]  # every review seen, by library id
    searched: list[tuple[FilterOption, int]]  # each filter option asked, and its result count
    tags: list[tuple[str, FilterOption]]  # (library id, option) for every review returned
    complete: bool  # every filter option was asked (False for a --max-filters test run)
    requests: int = 0


def filters_to_search(form: SearchForm) -> list[FilterOption]:
    """The filter options to ask about, starting with one of each CHECK_FIRST field."""
    options = [o for o in form.options if o.field not in SKIP_FIELDS]
    first = []
    for name in CHECK_FIRST:
        match = next((o for o in options if o.field == name), None)
        if match is not None:
            first.append(match)
    return first + [o for o in options if o not in first]


def collect(
    get: Callable[[str], str],
    max_filters: int = 0,
    base_url: str = BASE_URL,
    progress: Callable[[float], None] = lambda percent: None,
) -> Collection:
    """Query the unfiltered listing and each selected filter, retaining review/filter pairs.

    Check known filters first to detect a changed or ignored search form. Progress
    is reported as a percentage. A filter limit produces an incomplete collection."""
    form = parse_form(get(base_url))
    options = filters_to_search(form)
    print(f"OK    Read the search form: {len(options)} filter options to search", flush=True)

    total, listing = search(get, form, None, base_url)
    if total == 0:
        raise SiteChanged("the library lists no reviews at all")
    reviews = {r.library_id: r for r in listing}
    print(f"OK    The library lists {total} reviews", flush=True)
    progress(2)

    todo = options[:max_filters] if max_filters else options
    n_checks = sum(1 for o in options[: len(CHECK_FIRST)] if o.field in CHECK_FIRST)
    searched: list[tuple[FilterOption, int]] = []
    tags: list[tuple[str, FilterOption]] = []
    started = time.monotonic()
    for number, option in enumerate(todo, start=1):
        count, found = search(get, form, option, base_url)
        if count >= total:
            # No real filter matches every review, so the site must have ignored it.
            raise SiteChanged(
                f"'{option.group} / {option.label}' returned all {total} reviews, which means"
                " the site ignored the filter. Nothing was stored."
            )
        searched.append((option, count))
        progress(2 + 96 * number / len(todo))
        for review in found:
            reviews.setdefault(review.library_id, review)
            tags.append((review.library_id, option))
        if number == n_checks:
            print("OK    The site is filtering properly", flush=True)
        if number % 50 == 0 or number == len(todo):
            left = (time.monotonic() - started) / number * (len(todo) - number)
            print(
                f"      {number}/{len(todo)} filters searched, about {left / 60:.0f} min left",
                flush=True,
            )
    complete = len(todo) == len(options)
    print(f"OK    Searched {len(searched)} filters: {len(tags)} case details found", flush=True)
    return Collection(total, reviews, searched, tags, complete)


# Storing it.


def store_collection(
    conn, collection: Collection, git_commit: str, base_url: str = BASE_URL
) -> int:
    """Writes the collection as a new snapshot, all or nothing. Returns its snapshot id."""
    with conn.transaction():
        snapshot_id = conn.execute(
            "INSERT INTO evaluation.library_snapshots"
            " (source_url, git_commit, n_reviews, n_filters, complete)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING snapshot_id",
            (base_url, git_commit, collection.total, len(collection.searched), collection.complete),
        ).fetchone()[0]
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO evaluation.library_reviews (snapshot_id, library_id, full_name,"
                " title, csp, csp_region, uploaded, date_of_death)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        snapshot_id,
                        r.library_id,
                        r.full_name,
                        r.title,
                        r.csp,
                        r.csp_region,
                        r.uploaded,
                        r.date_of_death,
                    )
                    for r in collection.reviews.values()
                ],
            )
            cursor.executemany(
                "INSERT INTO evaluation.library_filters"
                " (snapshot_id, field, value, label, group_label, n_results)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (snapshot_id, o.field, o.value, o.label, o.group, count)
                    for o, count in collection.searched
                ],
            )
            cursor.executemany(
                "INSERT INTO evaluation.library_attributes (snapshot_id, library_id, field, value)"
                " VALUES (%s, %s, %s, %s)",
                [(snapshot_id, library_id, o.field, o.value) for library_id, o in collection.tags],
            )
    return snapshot_id


def loaded_document_links(conn) -> dict[str, dict[str, str | None]]:
    """Return each loaded dataset's review IDs and source links for metadata matching."""
    documents: dict[str, dict[str, str | None]] = {}
    rows = conn.execute(
        "SELECT dataset, doc_id,"
        " CASE WHEN source = 'Home Office website' THEN library_id END"
        " FROM evaluation.document_library_ids ORDER BY dataset, doc_id"
    ).fetchall()
    for dataset, doc_id, library_id in rows:
        documents.setdefault(dataset, {})[doc_id] = library_id
    return documents


def report_matches(
    collection: Collection,
    documents: dict[str, str | None],
    partial: bool = False,
    dataset: str = "our",
) -> dict[str, int]:
    """Prints how many of one dataset's reviews the library has, and has details for.
    Reviews with no library link (published somewhere else) are counted apart."""
    tagged = {library_id for library_id, _ in collection.tags}
    elsewhere = sorted(d for d, lib in documents.items() if lib is None)
    missing = sorted(
        d for d, lib in documents.items() if lib is not None and lib not in collection.reviews
    )
    in_library = len(documents) - len(elsewhere) - len(missing)
    with_details = sum(1 for lib in documents.values() if lib in tagged)
    log_progress(
        f"OK    {in_library} of the {len(documents)} {dataset} reviews are in the library listing"
    )
    if elsewhere:
        log_progress(
            f"NOTE  {len(elsewhere)} were published elsewhere (HALT, councils): no case details"
        )
    if collection.complete and not partial:
        log_progress(f"OK    {with_details} of them have at least one case detail")
    for doc_id in missing[:10]:
        log_progress(f"        has a library link but isn't in the listing: {doc_id}")
    return {
        "n_documents": len(documents),
        "n_in_library": in_library,
        "n_elsewhere": len(elsewhere),
        "n_with_details": with_details,
    }


def report_all_matches(
    collection: Collection, datasets: dict[str, dict[str, str | None]], partial: bool = False
) -> dict[str, dict[str, int]]:
    """report_matches for every dataset loaded."""
    if not datasets:
        log_progress("NOTE  No dataset is loaded yet, so there's nothing to match against")
    return {
        name: report_matches(collection, documents, partial, dataset=name)
        for name, documents in datasets.items()
    }


def log_to_mlflow(summary: dict) -> str | None:
    """Records the run in MLflow's 'case-metadata' experiment, if MLflow is set up."""
    if not use_mlflow():
        return None
    import mlflow

    mlflow.set_experiment("case-metadata")
    with mlflow.start_run(run_name=f"snapshot {summary['snapshot_id']}") as run:
        mlflow.log_params({k: summary[k] for k in ("snapshot_id", "git_commit", "complete")})
        metrics = {k: summary[k] for k in ("n_reviews", "n_filters", "n_details", "minutes")}
        for name, counts in summary.get("datasets", {}).items():
            metrics.update({f"{name}/{k}": v for k, v in counts.items()})
        mlflow.log_metrics(metrics)
        mlflow.set_tag("git_commit", summary["git_commit"])
    return run.info.run_id


# The whole run.


class CollectionError(RuntimeError):
    """The collection stopped part-way, so nothing was stored."""


def log_progress(message: str) -> None:
    """Flush a status line immediately so Windmill can display it during the run."""
    print(message, flush=True)


def run(
    max_filters: int = 0,
    delay: float = 1.0,
    dry_run: bool = False,
    base_url: str = BASE_URL,
    get: Callable[[str], str] | None = None,
) -> dict:
    """Collects everything, checks it, and stores it as a new snapshot (unless dry_run).
    Returns a summary. Raises CollectionError if the site didn't behave as expected."""
    fetcher = Fetcher(delay=delay)
    progress = ProgressBar()
    started = time.monotonic()
    try:
        collection = collect(get or fetcher.get, max_filters, base_url, progress)
    except SiteChanged as error:
        raise CollectionError(
            f"The library didn't behave as expected: {error}. Open the site in a browser to"
            " see what changed, then update f/dhr/O1/link_case_metadata/link_case_metadata.py. Nothing was stored."
        ) from error
    except RuntimeError as error:
        raise CollectionError(
            f"Couldn't get a page from the library: {error}. Nothing was stored; if the site"
            " is down, try again later."
        ) from error
    minutes = (time.monotonic() - started) / 60
    log_progress(f"OK    Done in {minutes:.0f} min ({fetcher.requests} requests)")

    summary = {
        "git_commit": GIT_COMMIT,
        "complete": collection.complete,
        "n_reviews": collection.total,
        "n_filters": len(collection.searched),
        "n_details": len(collection.tags),
        "minutes": round(minutes, 1),
    }
    if dry_run:
        for option, count in collection.searched:
            log_progress(f"      {count:5d}  {option.group} / {option.label}")
        try:
            with connect() as conn:
                apply_migrations(conn)
                report_all_matches(collection, loaded_document_links(conn), partial=True)
        except RuntimeError:
            pass  # no database configured (on a laptop): nothing to match against
        log_progress("Dry run: nothing was stored.")
        summary["counts"] = {f"{o.group} / {o.label}": n for o, n in collection.searched}
        return summary

    progress(99)  # storing; Windmill shows 100% once the run ends
    with connect() as conn:
        ran = apply_migrations(conn)
        if ran:
            log_progress(f"OK    Database tables updated: {', '.join(ran)}")
        snapshot_id = store_collection(conn, collection, GIT_COMMIT, base_url)
        log_progress(f"OK    Stored as snapshot {snapshot_id} in evaluation.library_*")
        if not collection.complete:
            log_progress(
                "NOTE  Only some filters were searched, so the case_* views will ignore it"
            )
        summary["datasets"] = report_all_matches(collection, loaded_document_links(conn))
    summary["snapshot_id"] = snapshot_id

    run_id = log_to_mlflow(summary)
    if run_id:
        log_progress(f"OK    Logged to MLflow: run {run_id} in experiment 'case-metadata'")
        summary["mlflow_run_id"] = run_id
    return summary


def main(max_filters: int = 0, delay_seconds: float = 1.0, dry_run: bool = False):
    """Windmill runs this. max_filters: only search this many filters (0: all of them).
    delay_seconds: the wait between requests to the site (at least 0.5). dry_run: collect
    and report, but store nothing."""
    if delay_seconds < 0.5:
        raise ValueError("delay_seconds must be at least 0.5, to be polite to the site")
    sys.stdout.reconfigure(line_buffering=True)  # show progress in the log as it happens
    return run(max_filters, delay_seconds, dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--delay", type=float, default=1.0, help="seconds between requests (at least 0.5)"
    )
    parser.add_argument(
        "--max-filters", type=int, default=0, help="only search this many filters (0: all)"
    )
    parser.add_argument("--dry-run", action="store_true", help="collect and report; store nothing")
    args = parser.parse_args()
    main(args.max_filters, args.delay, args.dry_run)
    log_progress("Done.")
