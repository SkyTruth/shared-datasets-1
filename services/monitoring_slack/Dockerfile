FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /app/
RUN uv sync --locked --no-dev --no-install-project
COPY scripts/slack_incident_api.py /app/scripts/slack_incident_api.py
COPY services/monitoring_slack /app/services/monitoring_slack
CMD ["/app/.venv/bin/python", "-m", "services.monitoring_slack.run"]
