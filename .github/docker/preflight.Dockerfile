FROM python:3.12.12-slim-bookworm

ARG GDAL_APT_VERSION=3.6.2+dfsg-1+b2

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git jq unzip libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
    libatspi2.0-0 libwayland-client0 libcups2 libdbus-1-3 libpango-1.0-0 \
    libcairo2 fonts-liberation fonts-noto-color-emoji \
    gdal-bin="${GDAL_APT_VERSION}" libgdal-dev="${GDAL_APT_VERSION}" build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /usr/local/bin/
COPY scripts/ci_install_tools.py scripts/ci_toolchain.py /opt/ci-source/scripts/
RUN cd /opt/ci-source && python scripts/ci_install_tools.py --destination /opt/ci \
    node22 node24 terraform gitleaks actionlint

ENV PATH="/opt/ci/node22/bin:/opt/ci/bin:${PATH}" \
    CI_NODE22_BIN="/opt/ci/node22/bin" \
    CI_NODE24_BIN="/opt/ci/node24/bin" \
    UV_CACHE_DIR="/work/uv-cache" \
    UV_PROJECT_ENVIRONMENT="/work/venv" \
    UV_LINK_MODE="copy" \
    GODEBUG="asyncpreemptoff=1" \
    SHARED_DATASETS_WORKDIR="/work/shared-datasets-1" \
    PYTHONUNBUFFERED="1"

WORKDIR /workspace
