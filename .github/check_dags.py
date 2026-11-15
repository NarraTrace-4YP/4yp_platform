"""Fails if any file in dags/ can't be loaded by Airflow (used by the GitHub Actions checks)."""

import sys
from pathlib import Path

from airflow.dag_processing.dagbag import DagBag

dags_folder = Path(__file__).resolve().parents[1] / "dags"
bag = DagBag(dag_folder=dags_folder, bundle_path=dags_folder, bundle_name="dhr")
for path, error in bag.import_errors.items():
    print(f"::error file={path}::{error}")
print(f"Loaded {len(bag.dags)} DAG(s): {', '.join(sorted(bag.dags)) or 'none'}")
sys.exit(1 if bag.import_errors else 0)
