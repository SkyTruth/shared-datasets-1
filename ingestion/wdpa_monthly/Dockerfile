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

# Keep dependency compilation cached while embedding the pinned source version.
RUN set -eux; \
    cd /go/pkg/mod/github.com/protomaps/go-pmtiles@${PMTILES_VERSION}; \
    GOOS="${TARGETOS}" GOARCH="${TARGETARCH}" CGO_ENABLED=0 \
        go build -ldflags "-X main.version=${PMTILES_VERSION#v}" -o /pmtiles .

FROM python:3.12-slim-bookworm

ARG PMTILES_VERSION=v1.30.1

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PMTILES_BUILD_VERSION=${PMTILES_VERSION}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gdal-bin=3.6.2+dfsg-1+b2 \
        libgdal-dev=3.6.2+dfsg-1+b2 \
        build-essential \
    && echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/bookworm-backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends -t bookworm-backports \
        tippecanoe=2.52.0-1~bpo12+1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pmtiles-builder /pmtiles /usr/local/bin/pmtiles

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra wdpa-native \
    && .venv/bin/python -c 'import numpy; from osgeo import gdal, gdal_array; assert gdal.VersionInfo("RELEASE_NAME") == "3.6.2"; assert numpy.__version__ == "1.26.4"; assert gdal_array.NumericTypeCodeToGDALTypeCode(numpy.dtype("uint8")) == gdal.GDT_Byte'
ENV PATH="/app/.venv/bin:${PATH}" \
    TMPDIR=/work/tmp \
    SHARED_DATASETS_WORKDIR=/work/shared-datasets-1 \
    CPL_TMPDIR=/work/tmp \
    SQLITE_TMPDIR=/work/tmp \
    TIPPECANOE_MAX_THREADS=4 \
    OGR_SQLITE_CACHE=64 \
    OGR_SQLITE_JOURNAL=OFF \
    OGR_SQLITE_SYNCHRONOUS=OFF

COPY ingestion ./ingestion
COPY catalog/shared-datasets-catalog.csv ./catalog/shared-datasets-catalog.csv
COPY scripts/catalog_csv.py ./scripts/catalog_csv.py
COPY scripts/feature_metadata_localization.py ./scripts/feature_metadata_localization.py
COPY scripts/feature_metadata_translation_reuse.py ./scripts/feature_metadata_translation_reuse.py
COPY scripts/translation_local_io.py ./scripts/translation_local_io.py
COPY scripts/pmtiles_zoom.py ./scripts/pmtiles_zoom.py
COPY scripts/release_feature_model.py ./scripts/release_feature_model.py
COPY scripts/slack_notify.py ./scripts/slack_notify.py
COPY scripts/vector_asset.py ./scripts/vector_asset.py
COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions
COPY scripts/local_wdpa_sample.py ./scripts/local_wdpa_sample.py
COPY scripts/local_ingestion_smoke.py ./scripts/local_ingestion_smoke.py
COPY docs/wdpa-processing-public-inputs.json ./docs/wdpa-processing-public-inputs.json
COPY scripts/wdpa_input_memory_probe.py /app/scripts/wdpa_input_memory_probe.py
COPY scripts/wdpa_processing_gate.py ./scripts/wdpa_processing_gate.py

# Revision identity does not affect dependency compilation or source bytes.
ARG SHARED_DATASETS_EXECUTOR_SHA
ENV SHARED_DATASETS_EXECUTOR_SHA=${SHARED_DATASETS_EXECUTOR_SHA}

CMD ["python", "-m", "ingestion.wdpa_monthly.run"]
