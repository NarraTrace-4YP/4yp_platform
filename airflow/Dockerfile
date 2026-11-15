# Airflow, plus the team's own Python environment.
FROM apache/airflow:3.4.0-python3.12

# Airflow add-ons: read pipelines from GitHub, connect to Blythe over SSH, talk to Postgres.
# Pinned to the versions Airflow 3.4.0 was tested with.
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
        apache-airflow-providers-git \
        apache-airflow-providers-ssh \
        apache-airflow-providers-postgres \
        --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.12.txt"

# The team's Python environment, kept separate from Airflow's so the two can't clash.
# It is built from the repo's lock file, so it matches what everyone has on their laptop.
# Pipeline tasks run with /opt/project-venv/bin/python (see dags/environment_check.py).
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
USER root
RUN mkdir -p /opt/project-venv && chown airflow:0 /opt/project-venv
USER airflow
COPY --chown=airflow:0 pyproject.toml uv.lock .python-version /tmp/project/
RUN cd /tmp/project \
    && UV_PROJECT_ENVIRONMENT=/opt/project-venv UV_PYTHON_DOWNLOADS=never \
       uv sync --frozen --no-install-project --no-dev --no-cache \
    && rm -rf /tmp/project
ENV DHR_PROJECT_PYTHON=/opt/project-venv/bin/python
