# MLflow tracking server with per-person logins, storing its records in Postgres.
FROM python:3.12-slim

RUN pip install --no-cache-dir "mlflow[auth]==3.18.0" "psycopg2-binary==2.9.13" \
    && useradd --create-home --uid 10001 mlflow \
    && mkdir -p /mlflow/artifacts \
    && chown -R mlflow /mlflow

COPY --chmod=755 start.sh /usr/local/bin/start-mlflow
USER mlflow
EXPOSE 5000
CMD ["start-mlflow"]
