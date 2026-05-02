FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/api/python/src:/app

WORKDIR /app

COPY api/python/src /app/api/python/src
COPY services /app/services

CMD ["python", "-m", "services.pmtiles_redirector.run"]
