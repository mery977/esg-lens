# Production image: API served by uvicorn, runs as a non-root user.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    ESG_DATA_DIR=/data PORT=8080

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY data/gold ./data/gold
COPY results ./results
RUN pip install ".[azure,gemini,anthropic]" && useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown app /data

USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "uvicorn esg_lens.api:app --host 0.0.0.0 --port ${PORT}"]
