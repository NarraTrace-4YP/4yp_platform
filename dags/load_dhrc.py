"""Load DHRC-100 into the database (corpus.documents), straight from Hugging Face.

Trigger it by hand: Dags > load_dhrc > Trigger. Leave "revision" as main to load the
latest version of the dataset, or give a commit id to load an exact older one. Running
it again for the same version replaces what was there, so it is always safe.
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
    tags=["stage-1", "corpus"],
    params={
        "revision": Param("main", type="string", description="Dataset branch, tag or commit"),
        "expected_documents": Param(
            100, type="integer", minimum=0, description="Fail if the count differs (0: skip)"
        ),
    },
)
def load_dhrc():
    # The settings reach the command as environment variables rather than being pasted
    # into it, so nothing typed into the form can change what the command does.
    @task.bash(
        env={
            "DHR_GIT_COMMIT": "{{ ti.bundle_instance.version or 'unknown' }}",
            "DHRC_REVISION": "{{ params.revision }}",
            "DHRC_EXPECTED": "{{ params.expected_documents }}",
        },
        append_env=True,
    )
    def ingest() -> str:
        return (
            f'cd "{REPO_ROOT}" && "{PROJECT_PYTHON}" -m dhr.ingest'
            ' --revision "$DHRC_REVISION" --expected-documents "$DHRC_EXPECTED"'
        )

    ingest()


load_dhrc()
