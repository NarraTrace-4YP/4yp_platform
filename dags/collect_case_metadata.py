"""Collect the case details the Home Office's DHR Library holds, into evaluation.library_*.

Trigger it by hand: Dags > collect_case_metadata > Trigger. A full run asks the library
about 900 questions, one a second, so it takes around half an hour. For a quick check
that the site still behaves as expected, set max_filters to 10 and tick dry_run: that
takes under a minute and stores nothing. Each full run is stored as a new snapshot, and
the evaluation.case_* views always use the newest one, so re-running is always safe.
"""

import os
from pathlib import Path

import pendulum
from airflow.sdk import Param, dag, task

# The team's Python environment (built from uv.lock), not Airflow's.
PROJECT_PYTHON = os.environ.get("DHR_PROJECT_PYTHON", "/opt/project-venv/bin/python")
# The repo, checked out at the commit this run is using.
REPO_ROOT = Path(__file__).resolve().parents[1]


@dag(
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="Europe/London"),
    catchup=False,
    tags=["stage-1", "evaluation"],
    params={
        "max_filters": Param(
            0, type="integer", minimum=0, description="Only search this many filters (0: all)"
        ),
        "delay_seconds": Param(
            1.0, type="number", minimum=0.5, description="Wait between requests to the site"
        ),
        "dry_run": Param(False, type="boolean", description="Collect and report, store nothing"),
    },
)
def collect_case_metadata():
    # The settings reach the command as environment variables rather than being pasted
    # into it, so nothing typed into the form can change what the command does.
    @task.bash(
        env={
            "DHR_GIT_COMMIT": "{{ ti.bundle_instance.version or 'unknown' }}",
            "LIBRARY_MAX_FILTERS": "{{ params.max_filters }}",
            "LIBRARY_DELAY": "{{ params.delay_seconds }}",
            "LIBRARY_DRY_RUN": "{{ params.dry_run }}",
            "PYTHONUNBUFFERED": "1",  # show progress in the log as it happens
        },
        append_env=True,
        execution_timeout=pendulum.duration(hours=3),
    )
    def collect() -> str:
        return (
            'DRY_RUN_FLAG=""; [ "$LIBRARY_DRY_RUN" = "True" ] && DRY_RUN_FLAG="--dry-run"; '
            f'cd "{REPO_ROOT}" && "{PROJECT_PYTHON}" -m dhr.library'
            ' --max-filters "$LIBRARY_MAX_FILTERS" --delay "$LIBRARY_DELAY" $DRY_RUN_FLAG'
        )

    collect()


collect_case_metadata()
