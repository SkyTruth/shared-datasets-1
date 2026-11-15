FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/api/python/src:/app

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "google-cloud-storage>=2.19.0,<3"

COPY api/python/src /app/api/python/src
COPY scripts /app/scripts
COPY services /app/services
COPY ingestion/__init__.py /app/ingestion/__init__.py
COPY ingestion/common/__init__.py ingestion/common/release_feature_model.py /app/ingestion/common/

RUN python -c "import services.feature_preview_service.run"

CMD ["python", "-m", "services.feature_preview_service.run"]
