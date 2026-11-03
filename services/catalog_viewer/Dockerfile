FROM python:3.12-slim@sha256:e70237b264442328526ff29d6b3c43bd8abcf6cbab78a949322fb69b7d91d6b6

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/api/python/src:/app

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "google-cloud-storage>=2.19.0,<3" "flatgeobuf==0.3.1"

COPY api/python/src /app/api/python/src
COPY scripts/compare_releases.py scripts/release_feature_model.py /app/scripts/
COPY ingestion/__init__.py /app/ingestion/__init__.py
COPY ingestion/common/__init__.py ingestion/common/decision_events.py ingestion/common/identity_index.py ingestion/common/publication.py ingestion/common/runtime.py ingestion/common/http.py /app/ingestion/common/
COPY ingestion/wdpa_monthly/__init__.py ingestion/wdpa_monthly/identity_review.py /app/ingestion/wdpa_monthly/
COPY services/__init__.py services/http_base.py /app/services/
COPY services/catalog_viewer /app/services/catalog_viewer
COPY services/feature_preview_service /app/services/feature_preview_service

CMD ["python", "-m", "services.catalog_viewer.run"]
