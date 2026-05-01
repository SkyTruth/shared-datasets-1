# skytruth-shared-datasets

Tiny Python resolver SDK for the SkyTruth shared datasets catalog.

The package reads the static CSV catalog and resolves dataset slugs to current
`latest/` objects in Cloud Storage. The canonical resolved identifier is always
the `gs://` URI. PMTiles browser-facing URLs default to the shared CDN path at
`https://tiles.skytruth.org/pmtiles/...`; other formats still default to public
`storage.googleapis.com` while public reads are available. Callers should treat
every browser URL as an access path, not the durable dataset identity.

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

For browser clients, keep the canonical `gs_uri` and use the shared CDN URL as
the browser-facing PMTiles path:

```python
pmtiles = catalog.resolve("wdpa-marine", format="pmtiles")
assert pmtiles.gs_uri.startswith("gs://")
assert pmtiles.url == "https://tiles.skytruth.org/pmtiles/wdpa-marine.pmtiles"
```

Applications with their own PMTiles route can still override only the browser
URL while leaving object identity unchanged:

```python
pmtiles = catalog.resolve("wdpa-marine", format="pmtiles", web_base_url="/pmtiles")
assert pmtiles.url == "/pmtiles/wdpa-marine.pmtiles"
```

PMTiles objects can live in a private bucket, but browser clients should not
fetch private GCS objects directly. The intended private-bucket model is for
Cerulean or another application layer to issue a Cloud CDN signed cookie and
load the normal `https://tiles.skytruth.org/pmtiles/{asset}.pmtiles` URL. The
SDK only keeps the canonical `gs_uri` separate from that browser-facing URL; it
does not sign cookies or expose CDN auth helpers.

Command line:

```bash
skytruth-datasets list
skytruth-datasets url wdpa-marine --format pmtiles
skytruth-datasets url wdpa-marine --format pmtiles --url-strategy public-gcs
skytruth-datasets url wdpa-marine --format pmtiles --web-base-url /pmtiles
skytruth-datasets fetch wdpa-marine --format fgb
skytruth-datasets fetch wdpa-marine --format fgb --access gcs
```

`Catalog.load()` reads the public bucket catalog by default. Pass a local path,
`gs://` URI, or HTTPS URL as `source` when callers need a specific catalog. Use
`Catalog.load_gcs()` when the catalog is private and readable through
Application Default Credentials.
