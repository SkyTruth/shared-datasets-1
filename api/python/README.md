# skytruth-shared-datasets

Tiny Python resolver SDK for the SkyTruth shared datasets catalog.

The package reads the static CSV catalog and resolves dataset slugs to current
`latest/` objects in Cloud Storage. The canonical resolved identifier is always
the `gs://` URI. The browser-facing URL defaults to public `storage.googleapis.com`
while public reads are available, but callers should treat that URL as a
convenience access path rather than the durable dataset identity.

## Installation

This package is currently distributed from this GitHub repository, not PyPI.
Install it from the `api/python` subdirectory:

```bash
pip install "skytruth-shared-datasets @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

If your access to the repository is through SSH:

```bash
pip install "skytruth-shared-datasets @ git+ssh://git@github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For service-account-mediated GCS reads, install the optional GCS extra:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

Or with SSH:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+ssh://git@github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For production consumers, pin a tag or commit SHA instead of `main`:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@<tag-or-sha>#subdirectory=api/python"
```

For local development inside this repository:

```bash
pip install -e "api/python[gcs]"
```

## Basic usage

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load()
wdpa = catalog.resolve("wdpa-marine", format="fgb")
print(wdpa.gs_uri)
print(wdpa.url)
path = catalog.fetch("wdpa-marine", format="fgb")
```

The default install has no runtime dependencies. Service-account-mediated GCS
reads require the `gcs` extra and Application Default Credentials:

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load_gcs()
path = catalog.fetch("wdpa-marine", format="fgb", access="gcs")
```

For browser clients that serve PMTiles through an application CDN, keep the
canonical `gs_uri` and shape only the browser-facing URL:

```python
pmtiles = catalog.resolve("wdpa-marine", format="pmtiles", web_base_url="/pmtiles")
assert pmtiles.gs_uri.startswith("gs://")
assert pmtiles.url == "/pmtiles/wdpa-marine.pmtiles"
```

PMTiles objects can live in a private bucket, but browser clients should not
fetch private GCS objects directly. The intended private-bucket model is for
Cerulean or another application layer to expose a normal HTTPS PMTiles path,
backed by private GCS plus Cloud CDN signed cookies or signed URLs. The SDK only
keeps the canonical `gs_uri` separate from that browser-facing URL.

Command line:

```bash
skytruth-datasets list
skytruth-datasets url wdpa-marine --format pmtiles
skytruth-datasets url wdpa-marine --format pmtiles --url-strategy cdn --web-base-url /pmtiles
skytruth-datasets fetch wdpa-marine --format fgb
skytruth-datasets fetch wdpa-marine --format fgb --access gcs
```

`Catalog.load()` reads the public bucket catalog by default. Pass a local path,
`gs://` URI, or HTTPS URL as `source` when callers need a specific catalog. Use
`Catalog.load_gcs()` when the catalog is private and readable through
Application Default Credentials.
