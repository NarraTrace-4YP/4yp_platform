"""Tests for f/dhr/collect_case_metadata.py, against a made-up imitation of the DHR Library.

The pages here copy the real site's markup (as of November 2026) but hold invented reviews,
so no real DHR content goes in the repo. If the real site changes its markup, update the
markup here to match, then fix the script until these pass again.
"""

import threading
import urllib.parse
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from f.dhr import collect_case_metadata as library

BASE = "https://library.example/"
SEX = "Victim-Specific Information.Victims.Sex of victim[]"
AGE = "Victim-Specific Information.Victims.Age.Age at time of death[]"
CATEGORY = "internal.dhr-category[]"
KEYWORDS = "Keywords[]"
UPLOADED = "internal.upload-date[]"


# ----------------------------------------------------------------- the imitation site


def checkbox(field, value, label):
    box_id = f"{field}--{value}".replace(" ", "-").replace(".", "-").lower()
    return f"""
    <div class="govuk-checkboxes__item">
        <input class="govuk-checkboxes__input" id="{escape(box_id)}" name="{escape(field)}"
               type="checkbox" value="{escape(value)}">
        <label class="govuk-label govuk-checkboxes__label" for="{escape(box_id)}">
            {escape(label)}
        </label>
    </div>"""


def fieldset(legend, boxes):
    return f"""
    <div class="govuk-form-group"><fieldset class="govuk-fieldset">
        <legend class="govuk-fieldset__legend"><h2 class="govuk-fieldset__heading">
            {legend}</h2></legend>
        <div class="govuk-checkboxes">{"".join(boxes)}</div>
    </fieldset></div>"""


def accordion_section(slug, heading, content):
    return f"""
    <div class="govuk-accordion__section">
        <div class="govuk-accordion__section-header"><h2 class="govuk-accordion__section-heading">
            <span class="govuk-accordion__section-button" id="{slug}--heading">{heading}</span>
        </h2></div>
        <div class="govuk-accordion__section-content">{content}</div>
    </div>"""


FORM = f"""
<form action="{BASE}cookies" method="post">
    <input type="hidden" name="redirectUrl" value="/">
</form>
<form action="{BASE}" method="get">
    <label class="govuk-label" id="keywords--label" for="keywords">Filter by theme</label>
    <select class="govuk-select" name="{KEYWORDS}" id="keywords-select"
            aria-labelledby="keywords--label">
        <option value="">All</option>
        <option value="Theme &amp; more">Theme &amp; more</option>
    </select>
    <div class="govuk-accordion" data-module="govuk-accordion">
        {
    accordion_section(
        "dhr-categories",
        "Most filtered for DHRs",
        checkbox(CATEGORY, "Intimate-partner homicide", "Intimate-partner homicide"),
    )
}
        {
    accordion_section(
        "about-the-victim",
        "About the victim",
        fieldset("Sex", [checkbox(SEX, "Female", "Female"), checkbox(SEX, "Male", "Male")])
        + fieldset("Age (years)", [checkbox(AGE, "16,19", "16 - 19")]),
    )
}
        {
    accordion_section(
        "about-the-death",
        "About the death",
        fieldset("DHR upload date", [checkbox(UPLOADED, "2022,2022", "2022")]),
    )
}
    </div>
    <input type="hidden" name="pagination[sortField]" value="uploadDate">
    <input type="hidden" name="pagination[sortDirection]" value="desc">
    <input type="hidden" name="pagination[pageSize]" value="10">
    <button type="submit">Show results</button>
</form>"""


def card(library_id, full_name, title, csp="Somewhere", region="North West"):
    def detail(caption, value):
        return f"""
        <div class="govuk-grid-column-one-quarter">
            <span class="govuk-caption-m">{caption}</span>
            <span class="govuk-label dhrr-results-card--detail-value">{escape(value)}</span>
        </div>"""

    return f"""
    <section aria-label="DHR result {escape(full_name)}">
        <div><h3 class="govuk-heading-s"><span class="govuk-caption-m">DHR Title</span>
            {escape(title)}</h3></div>
        <div class="govuk-grid-row">
            {detail("CSP", csp)}{detail("CSP Region", region)}
            {detail("Uploaded on", "March 2025")}{detail("Date of death", "May 2021")}
        </div>
        <a href="{BASE}download/{library_id}" target="_blank">View</a>
        <a href="{BASE}download/{library_id}" download="{escape(full_name)}">Download</a>
    </section>"""


def page(body):
    return f"<!DOCTYPE html><html><body><main>{FORM}{body}</main></body></html>"


def results_page(cards, first, total):
    if not cards:
        return page("<p>No DHRs match your filters.</p>")
    count = (
        f'<span class="govuk-caption-m">Showing results {first}\n to '
        f"{first + len(cards) - 1}\n of {total}</span>"
    )
    return page(count + "".join(cards))


# Six invented reviews, newest first, and the filter options each one matches.
REVIEWS = [
    (f"{n:024x}", f"Person{n} - Town{n} - 202{n % 10}/a - DHR", f"Person{n}") for n in range(1, 7)
]
TAGS = {
    REVIEWS[0][0]: {(SEX, "Female"), (CATEGORY, "Intimate-partner homicide")},
    REVIEWS[1][0]: {(SEX, "Female"), (AGE, "16,19"), (KEYWORDS, "Theme & more")},
    REVIEWS[2][0]: {(SEX, "Male")},
    REVIEWS[3][0]: {(SEX, "Female"), (CATEGORY, "Intimate-partner homicide")},
    REVIEWS[4][0]: {(SEX, "Female")},
    REVIEWS[5][0]: set(),
}


class FakeSite:
    """Answers search addresses like the real site would, and remembers what was asked."""

    def __init__(self, ignore_filters=False, ignore_page_number=False):
        self.ignore_filters = ignore_filters
        self.ignore_page_number = ignore_page_number
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        query = urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query, keep_blank_values=True)
        if not query:
            return page("<p>Showing all DHRs</p>")
        params = dict(query)
        size = int(params.get("pagination[pageSize]", 10))
        number = 0 if self.ignore_page_number else int(params.get("pagination[pageNumber]", 0))
        chosen = {(k, v) for k, v in query if v and not k.startswith("pagination")}
        matching = [
            r for r in REVIEWS if self.ignore_filters or chosen <= TAGS[r[0]]
        ]  # every ticked option must match, like the real site
        shown = matching[number * size : (number + 1) * size]
        return results_page([card(*r) for r in shown], number * size + 1, len(matching))


@pytest.fixture
def small_pages(monkeypatch):
    """Two reviews per page, so the six reviews need several pages."""
    monkeypatch.setattr(library, "PAGE_SIZE", 2)


# ----------------------------------------------------------------- reading pages


def test_form_options_labels_and_groups():
    form = library.parse_form(page(""))
    by_value = {o.value: o for o in form.options}
    assert set(by_value) == {
        "Theme & more", "Intimate-partner homicide", "Female", "Male", "16,19", "2022,2022"
    }  # fmt: skip
    assert by_value["16,19"].label == "16 - 19"
    assert by_value["16,19"].group == "About the victim / Age (years)"
    assert by_value["Female"].group == "About the victim / Sex"
    assert by_value["Intimate-partner homicide"].group == "Most filtered for DHRs"
    assert by_value["Theme & more"].group == "Filter by theme"
    # The cookie form's hidden field isn't part of the search.
    assert ("hidden", "redirectUrl", "/") not in form.controls


def test_skipped_fields_left_out_and_checks_go_first():
    options = library.filters_to_search(library.parse_form(page("")))
    assert [o.value for o in options[:2]] == ["Female", "Theme & more"]
    assert all(o.field != UPLOADED for o in options)
    assert len(options) == 5


def test_search_url_matches_what_a_browser_sends():
    form = library.parse_form(page(""))
    option = next(o for o in form.options if o.value == "Intimate-partner homicide")
    assert library.search_url(form, option, 0, BASE) == (
        BASE + "?Keywords%5B%5D=&internal.dhr-category%5B%5D=Intimate-partner+homicide"
        "&pagination%5BsortField%5D=uploadDate&pagination%5BsortDirection%5D=desc"
        "&pagination%5BpageSize%5D=100"
    )


def test_search_url_for_a_drop_down_and_a_later_page():
    form = library.parse_form(page(""))
    option = next(o for o in form.options if o.field == KEYWORDS)
    url = library.search_url(form, option, 2, BASE)
    assert "Keywords%5B%5D=Theme+%26+more&" in url
    assert url.endswith("&pagination%5BpageNumber%5D=2")


def test_results_cards_and_count():
    result = library.parse_results(results_page([card(*REVIEWS[0]), card(*REVIEWS[1])], 1, 9))
    assert (result.first, result.last, result.total) == (1, 2, 9)
    first = result.reviews[0]
    assert first == library.Review(
        library_id=REVIEWS[0][0],
        full_name=REVIEWS[0][1],
        title="Person1",
        csp="Somewhere",
        csp_region="North West",
        uploaded="March 2025",
        date_of_death="May 2021",
    )


def test_no_results_is_zero_not_an_error():
    result = library.parse_results(results_page([], 0, 0))
    assert (result.reviews, result.total) == ([], 0)


def test_error_page_is_not_mistaken_for_no_results():
    with pytest.raises(library.SiteChanged, match="search form"):
        library.parse_results("<html><body><h1>Service unavailable</h1></body></html>")


def test_count_that_disagrees_with_the_cards_is_caught():
    html = page(
        '<span class="govuk-caption-m">Showing results 1 to 5 of 9</span>' + card(*REVIEWS[0])
    )
    with pytest.raises(library.SiteChanged, match="shows 1"):
        library.parse_results(html)


# ----------------------------------------------------------------- collecting


def test_collects_every_review_and_detail(small_pages):
    site = FakeSite()
    collection = library.collect(site.get, base_url=BASE)
    assert collection.total == 6
    assert set(collection.reviews) == {r[0] for r in REVIEWS}
    assert collection.complete
    found = {(lib, (o.field, o.value)) for lib, o in collection.tags}
    expected = {(lib, tag) for lib, tags in TAGS.items() for tag in tags if tag[0] != UPLOADED}
    assert found == expected
    counts = {o.value: n for o, n in collection.searched}
    assert counts == {
        "Female": 4, "Theme & more": 1, "Intimate-partner homicide": 2, "Male": 1, "16,19": 1
    }  # fmt: skip
    # Female has 4 results at 2 a page, so it needed a second page.
    assert any("pageNumber%5D=1" in url and "Female" in url for url in site.urls)


def test_test_run_searches_only_some_filters():
    collection = library.collect(FakeSite().get, max_filters=2, base_url=BASE)
    assert [o.value for o, _ in collection.searched] == ["Female", "Theme & more"]
    assert not collection.complete


def test_site_ignoring_filters_stops_the_run():
    with pytest.raises(library.SiteChanged, match="ignored the filter"):
        library.collect(FakeSite(ignore_filters=True).get, base_url=BASE)


def test_site_ignoring_page_numbers_stops_the_run(small_pages):
    with pytest.raises(library.SiteChanged, match="page 2"):
        library.collect(FakeSite(ignore_page_number=True).get, base_url=BASE)


def test_match_report_counts_our_reviews():
    collection = library.collect(FakeSite().get, base_url=BASE)
    ours = {"A": REVIEWS[0][0], "B": REVIEWS[5][0], "C": "f" * 24, "D": None}
    assert library.report_matches(collection, ours) == {
        "n_documents": 4,
        "n_in_library": 2,
        "n_with_details": 1,
    }


def test_dry_run_stores_nothing(monkeypatch, capsys):
    monkeypatch.delenv("DHR_DATABASE_URL", raising=False)
    monkeypatch.delenv("WM_TOKEN", raising=False)
    summary = library.run(max_filters=2, dry_run=True, base_url=BASE, get=FakeSite().get)
    assert "Dry run: nothing was stored." in capsys.readouterr().out
    assert summary["counts"] == {
        "About the victim / Sex / Female": 4,
        "Filter by theme / Theme & more": 1,
    }
    assert not summary["complete"]


def test_broken_site_fails_cleanly():
    with pytest.raises(library.CollectionError, match="didn't behave as expected"):
        library.run(dry_run=True, base_url=BASE, get=FakeSite(ignore_filters=True).get)


def test_delay_below_half_a_second_is_refused():
    with pytest.raises(ValueError, match="at least 0.5"):
        library.main(delay_seconds=0.1, dry_run=True)


# ----------------------------------------------------------------- fetching


def test_fetcher_retries_a_server_error(monkeypatch):
    answers = [503, 200]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = answers.pop(0)
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"status {status}, agent {self.headers['User-Agent']}".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    try:
        fetcher = library.Fetcher(delay=0, waits=(0,))
        text = fetcher.get(f"http://127.0.0.1:{server.server_port}/")
    finally:
        server.shutdown()
    assert text.startswith("status 200")
    assert "NarraTrace-4YP" in text
    assert fetcher.requests == 2
