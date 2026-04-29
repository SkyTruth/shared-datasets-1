FROM golang:1.25-bookworm AS pmtiles-builder

ARG PMTILES_VERSION=v1.30.1

RUN go install github.com/protomaps/go-pmtiles@${PMTILES_VERSION} \
    && if [ -x /go/bin/go-pmtiles ]; then cp /go/bin/go-pmtiles /pmtiles; else cp /go/bin/pmtiles /pmtiles; fi

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        gdal-bin \
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

CMD ["python", "-m", "ingestion.wdpa_monthly.run"]
