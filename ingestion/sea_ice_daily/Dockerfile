FROM --platform=$BUILDPLATFORM golang:1.25-bookworm AS pmtiles-builder

ARG PMTILES_VERSION=v1.30.1
ARG TARGETOS
ARG TARGETARCH

RUN set -eux; \
    GOOS="${TARGETOS}" GOARCH="${TARGETARCH}" CGO_ENABLED=0 \
        go install github.com/protomaps/go-pmtiles@${PMTILES_VERSION}; \
    for candidate in \
        "/go/bin/${TARGETOS}_${TARGETARCH}/go-pmtiles" \
        "/go/bin/${TARGETOS}_${TARGETARCH}/pmtiles" \
        "/go/bin/go-pmtiles" \
        "/go/bin/pmtiles"; do \
        if [ -x "$candidate" ]; then cp "$candidate" /pmtiles; exit 0; fi; \
    done; \
    echo "pmtiles binary not found" >&2; \
    exit 1

FROM python:3.12-slim-bookworm

ARG SHARED_DATASETS_EXECUTOR_SHA
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SHARED_DATASETS_EXECUTOR_SHA=${SHARED_DATASETS_EXECUTOR_SHA}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gdal-bin \
        python3-gdal \
    && echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/bookworm-backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends -t bookworm-backports \
        tippecanoe \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pmtiles-builder /pmtiles /usr/local/bin/pmtiles

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir google-cloud-storage

COPY ingestion ./ingestion
COPY scripts/pmtiles_zoom.py ./scripts/pmtiles_zoom.py
COPY scripts/release_feature_model.py ./scripts/release_feature_model.py
COPY scripts/slack_notify.py ./scripts/slack_notify.py
COPY scripts/vector_asset.py ./scripts/vector_asset.py
COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions
COPY docs/assets/ims-sea-ice-extent.md /tmp/ims-asset-doc.md
RUN python -c 'from pathlib import Path; text = Path("/tmp/ims-asset-doc.md").read_text(); assert text.startswith("---\n"); body = text.split("\n---\n", 1)[1].strip() + "\n"; Path("/app/asset_README.md").write_text(body)' \
    && rm /tmp/ims-asset-doc.md

CMD ["python", "-m", "ingestion.sea_ice_daily.run"]
