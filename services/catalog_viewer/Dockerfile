FROM python:3.12-slim@sha256:e70237b264442328526ff29d6b3c43bd8abcf6cbab78a949322fb69b7d91d6b6

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/api/python/src:/app

WORKDIR /app

RUN python -m pip install --no-cache-dir \
    "google-cloud-storage>=2.19.0,<3" "flatgeobuf==0.3.1"

COPY api/python/src /app/api/python/src
COPY scripts /app/scripts
COPY services /app/services

CMD ["python", "-m", "services.catalog_viewer.run"]
