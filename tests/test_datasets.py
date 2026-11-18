"""Tests for f/dhr/datasets.py, the list of datasets the pipelines and case viewer know."""

import importlib
import re
import typing
from pathlib import Path

import pytest

from f.dhr.datasets import DATASETS, check_dataset

APP = Path(__file__).parent.parent / "f/dhr/O1/app.raw_app/shared.ts"


def test_known_dataset_gives_its_settings():
    assert check_dataset("DHRC-RAW")["repo_id"] == "4YP/dhr-corpus-raw-copy"


def test_unknown_dataset_names_the_choices():
    with pytest.raises(ValueError, match="choose one of DHRC-100, DHRC-RAW"):
        check_dataset("dhrc-100")


def test_o1_app_offers_the_same_datasets():
    # The app's dataset lists are written out in shared.ts; they must be exactly these.
    match = re.search(r"const DATASETS = \[(.*?)\]", APP.read_text(encoding="utf-8"))
    assert match, "shared.ts should have a line: const DATASETS = [...]"
    assert re.findall(r"'([^']+)'", match.group(1)) == list(DATASETS)


@pytest.mark.parametrize(
    "module",
    [
        "O1.load_reviews.load_reviews",
        "O1.group_blocks_by_section.group_blocks_by_section",
        "O1.link_case_metadata.get_reviews",
        "O1.link_case_metadata.get_case_details",
    ],
)
def test_windmill_dropdowns_offer_the_same_datasets(module):
    # Windmill shows main's Literal[...] as a dropdown; it must list exactly these datasets.
    main = importlib.import_module(f"f.dhr.{module}").main
    assert list(typing.get_args(typing.get_type_hints(main)["dataset"])) == list(DATASETS)
