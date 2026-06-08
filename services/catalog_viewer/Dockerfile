FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/api/python/src:/app

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "google-cloud-firestore>=2.21.0,<3" \
    "google-cloud-storage>=2.19.0,<3"

COPY api/python/src /app/api/python/src
COPY scripts /app/scripts
COPY services /app/services

CMD ["python", "-m", "services.catalog_viewer.run"]
