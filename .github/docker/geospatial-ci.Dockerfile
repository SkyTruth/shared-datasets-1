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

ARG GDAL_APT_VERSION=3.6.2+dfsg-1+b2
ARG TIPPECANOE_APT_VERSION=2.52.0-1~bpo12+1
ARG UV_VERSION=0.11.8

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gdal-bin="${GDAL_APT_VERSION}" \
        python3-gdal="${GDAL_APT_VERSION}" \
    && echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/bookworm-backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends -t bookworm-backports \
        tippecanoe="${TIPPECANOE_APT_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pmtiles-builder /pmtiles /usr/local/bin/pmtiles

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /workspace
