# skytruth-shared-datasets

Tiny Python resolver SDK for the SkyTruth shared datasets catalog.

The package reads the static CSV catalog and resolves dataset slugs to current
`latest/` objects in Cloud Storage. The canonical resolved identifier is always
the `gs://` URI. The browser-facing URL defaults to public `storage.googleapis.com`
while public reads are available, but callers should treat that URL as a
convenience access path rather than the durable dataset identity.

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load()
wdpa = catalog.resolve("wdpa-marine", format="fgb")
print(wdpa.gs_uri)
print(wdpa.url)
path = catalog.fetch("wdpa-marine", format="fgb")
```

The default install has no runtime dependencies. For service-account-mediated
GCS reads, install the optional GCS extra and use Application Default
Credentials:

```bash
pip install "skytruth-shared-datasets[gcs]"
```

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

Command line:

```bash
skytruth-datasets list
skytruth-datasets url wdpa-marine --format pmtiles
skytruth-datasets url wdpa-marine --format pmtiles --url-strategy cdn --web-base-url /pmtiles
skytruth-datasets fetch wdpa-marine --format fgb
skytruth-datasets fetch wdpa-marine --format fgb --access gcs
```

`Catalog.load()` reads the public bucket catalog by default and falls back to the
packaged snapshot only when the public URL appears unavailable. It raises on
malformed live catalogs and permission failures so stale packaged metadata does
not hide a broken or private catalog. Pass a local path, `gs://` URI, HTTPS URL,
or `"packaged"` as `source` when callers need a specific catalog. Use
`Catalog.load_gcs()` when the catalog is private and readable through
Application Default Credentials.
