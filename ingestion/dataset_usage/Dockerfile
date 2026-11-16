FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
RUN python -m pip install --no-cache-dir "google-cloud-storage>=2.19.0,<3"
COPY ingestion/dataset_usage /app/ingestion/dataset_usage
COPY scripts/catalog_csv.py /app/scripts/catalog_csv.py
COPY catalog/shared-datasets-catalog.csv catalog/dataset-usage.json catalog/dataset-usage-activation.json /app/catalog/
ENTRYPOINT ["python", "-m", "ingestion.dataset_usage.run"]
