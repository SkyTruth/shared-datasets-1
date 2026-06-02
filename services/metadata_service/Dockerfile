FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/api/python/src:/app

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "google-cloud-firestore>=2.20.0,<3" \
    "google-cloud-storage>=2.19.0,<3"

COPY api/python/src /app/api/python/src
COPY services /app/services

CMD ["python", "-m", "services.metadata_service.run"]
