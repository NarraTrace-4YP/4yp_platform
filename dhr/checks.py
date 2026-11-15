"""Checks that the shared environment works, from inside a pipeline task.

Run by the "environment_check" DAG. It confirms that:
  1. the project's Python packages are installed,
  2. the database is reachable and has the search extensions and the three schemas,
  3. a run can be logged to MLflow, tagged with the Git commit Airflow ran.

Run it on your laptop too:  uv run python -m dhr.checks
(with DHR_DATABASE_URL and the MLFLOW_* variables set; see the README).
"""

import importlib.metadata
import os
import sys

PACKAGES = [
    "langgraph",
    "langchain-openai",
    "mlflow-skinny",
    "psycopg",
    "pgvector",
    "huggingface-hub",
    "pandas",
    "pyarrow",
    "pydantic",
]
EXTENSIONS = {"vector", "pg_search"}
SCHEMAS = {"corpus", "evaluation", "scratch"}


def check_packages() -> dict[str, str]:
    return {name: importlib.metadata.version(name) for name in PACKAGES}


def check_database(url: str) -> dict[str, str]:
    import psycopg

    with psycopg.connect(url, connect_timeout=10) as conn:
        server = conn.execute("SHOW server_version").fetchone()[0]
        installed = {row[0] for row in conn.execute("SELECT extname FROM pg_extension")}
        schemas = {row[0] for row in conn.execute("SELECT nspname FROM pg_namespace")}
    missing = sorted((EXTENSIONS - installed) | (SCHEMAS - schemas))
    if missing:
        raise RuntimeError(f"database is missing: {', '.join(missing)}")
    return {"postgres": server, "extensions": ", ".join(sorted(EXTENSIONS))}


def log_to_mlflow(details: dict[str, str]) -> str:
    import mlflow

    mlflow.set_experiment("environment-check")
    with mlflow.start_run() as run:
        mlflow.log_params(details)
        mlflow.set_tag("git_commit", os.environ.get("DHR_GIT_COMMIT", "unknown"))
        mlflow.log_metric("ok", 1)
    return run.info.run_id


def main() -> int:
    failures = []
    details: dict[str, str] = {}

    steps = [
        ("Python packages", check_packages),
        ("Database", lambda: check_database(os.environ["DHR_DATABASE_URL"])),
    ]
    for label, step in steps:
        try:
            result = step()
            details.update(result)
            print(f"OK    {label}: " + ", ".join(f"{k} {v}" for k, v in result.items()))
        except Exception as error:  # noqa: BLE001 - report every problem, not just the first
            failures.append(label)
            print(f"FAIL  {label}: {error}")

    try:
        run_id = log_to_mlflow(details)
        print(f"OK    MLflow: logged run {run_id} in experiment 'environment-check'")
    except Exception as error:  # noqa: BLE001
        failures.append("MLflow")
        print(f"FAIL  MLflow: {error}")

    print(f"      Git commit: {os.environ.get('DHR_GIT_COMMIT', 'unknown')}")
    if failures:
        print(f"Environment check failed: {', '.join(failures)}")
        return 1
    print("Environment check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
