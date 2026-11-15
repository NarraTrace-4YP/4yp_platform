"""Environment check. Trigger it once after setting up the server, and after big changes.

Also a template for real pipeline steps: Airflow only schedules the work, and the
work itself runs as a module from the dhr/ package, in the project's own Python
environment, using the code from the exact commit this run was started from.
"""

import os
from pathlib import Path

import pendulum
from airflow.sdk import dag, task

# The team's Python environment (built from uv.lock), not Airflow's.
PROJECT_PYTHON = os.environ.get("DHR_PROJECT_PYTHON", "/opt/project-venv/bin/python")
# The repo, checked out at the commit this run is using.
REPO_ROOT = Path(__file__).resolve().parents[1]


@dag(
    schedule=None,
    start_date=pendulum.datetime(2026, 11, 1, tz="Europe/London"),
    catchup=False,
    tags=["setup"],
)
def environment_check():
    @task.bash(
        env={"DHR_GIT_COMMIT": "{{ ti.bundle_instance.version or 'unknown' }}"},
        append_env=True,
    )
    def run_checks() -> str:
        return f'cd "{REPO_ROOT}" && "{PROJECT_PYTHON}" -m dhr.checks'

    run_checks()


environment_check()
