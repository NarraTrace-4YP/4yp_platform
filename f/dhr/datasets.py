"""The DHR datasets the pipelines know about, kept separate by name.

DHRC-100 is the validated set of 100 reviews. DHRC-RAW is every review the team gathered
(702, including the 100), as raw automated output that hasn't been checked by hand.
Each is loaded on its own (f/dhr/load_dhrc) and labelled with its name, so a wiki can be
built from either one or both.

"reviews" is how many review files each should have, and "strict" whether a single review
that doesn't read as a document stops the whole load (otherwise it's skipped and listed).
"""

DATASETS = {
    "DHRC-100": {"repo_id": "4YP/dhr-corpus-100-copy", "reviews": 100, "strict": True},
    "DHRC-RAW": {"repo_id": "4YP/dhr-corpus-raw-copy", "reviews": 702, "strict": False},
}


def check_dataset(name: str) -> dict:
    """The settings for one dataset, or a clear error naming the ones there are."""
    if name not in DATASETS:
        raise ValueError(f"Unknown dataset {name!r}: choose one of {', '.join(DATASETS)}")
    return DATASETS[name]
