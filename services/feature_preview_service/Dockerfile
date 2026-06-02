FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PORT=8080

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "google-cloud-firestore>=2.21.0,<3" \
    "google-cloud-storage>=2.19.0,<3"

COPY services ./services

CMD ["python", "-m", "services.feature_preview_service.run"]
