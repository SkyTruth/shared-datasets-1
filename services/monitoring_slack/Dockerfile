FROM python:3.12.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock README.md /app/
RUN uv sync --locked --no-dev --no-install-project
COPY scripts/slack_incident_api.py scripts/slack_notify.py scripts/operational_retry.py scripts/gcp_read_retry.py /app/scripts/
COPY services/__init__.py /app/services/__init__.py
COPY services/monitoring_slack /app/services/monitoring_slack
ENTRYPOINT ["python", "-m", "services.monitoring_slack.run"]
