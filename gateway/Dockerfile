# The LLM gateway (gateway/README.md). Built by `docker compose up -d --build` from the
# repository root, so it can copy the Blythe client it shares with Windmill (f/dhr/blythe.py).
FROM python:3.12-slim

WORKDIR /app
COPY gateway/requirements.txt gateway/requirements.txt
RUN pip install --no-cache-dir -r gateway/requirements.txt

COPY f/dhr/blythe.py f/dhr/blythe.py
COPY gateway gateway

# Runs as an ordinary user; only /data (usage records) is written.
RUN useradd --system --home /app gateway && mkdir -p /data && chown gateway /data
USER gateway
ENV PYTHONUNBUFFERED=1 GATEWAY_DATA=/data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
# One process: each person's session (their Blythe login and job) lives in its memory.
CMD ["uvicorn", "--factory", "gateway.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
