FROM --platform=$BUILDPLATFORM golang:1.25-bookworm AS pmtiles-builder

ARG PMTILES_VERSION=v1.30.1
ARG TARGETOS
ARG TARGETARCH

# Retain verified module downloads across failed builds. Go still checks sums;
# target-specific compiler caches cannot mix AMD64 and ARM build products.
RUN --mount=type=cache,id=mpatlas-go-modules,target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,id=mpatlas-go-build-${TARGETARCH},target=/root/.cache/go-build,sharing=locked \
    set -eux; \
    GOOS="${TARGETOS}" GOARCH="${TARGETARCH}" CGO_ENABLED=0 \
        go install -ldflags "-X main.version=${PMTILES_VERSION#v}" github.com/protomaps/go-pmtiles@${PMTILES_VERSION}; \
    for candidate in \
        "/go/bin/${TARGETOS}_${TARGETARCH}/go-pmtiles" \
        "/go/bin/${TARGETOS}_${TARGETARCH}/pmtiles" \
        "/go/bin/go-pmtiles" \
        "/go/bin/pmtiles"; do \
        if [ -x "$candidate" ]; then cp "$candidate" /pmtiles; exit 0; fi; \
    done; \
    echo "pmtiles binary not found" >&2; \
    exit 1

FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gdal-bin=3.6.2+dfsg-1+b2 \
    && echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/bookworm-backports.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends -t bookworm-backports \
        tippecanoe=2.52.0-1~bpo12+1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=pmtiles-builder /pmtiles /usr/local/bin/pmtiles

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
ENV PATH="/app/.venv/bin:${PATH}"

COPY ingestion ./ingestion
COPY scripts/pmtiles_zoom.py ./scripts/pmtiles_zoom.py
COPY scripts/release_feature_model.py ./scripts/release_feature_model.py
COPY scripts/slack_notify.py ./scripts/slack_notify.py
COPY scripts/vector_asset.py ./scripts/vector_asset.py

CMD ["python", "-m", "ingestion.mpatlas_monthly.run"]
