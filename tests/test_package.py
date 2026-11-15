"""A first test so the test run has something to do. Add real tests alongside it."""

from dhr import checks


def test_checks_module_lists_required_packages():
    assert "langgraph" in checks.PACKAGES
    assert {"corpus", "evaluation"} <= checks.SCHEMAS
