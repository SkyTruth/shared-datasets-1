# skytruth-shared-datasets

Tiny Python resolver SDK for the public SkyTruth shared datasets catalog.

The package reads the static CSV catalog and resolves dataset slugs to current
`latest/` objects in Cloud Storage. It has no runtime dependencies and does not
require Google Cloud credentials for public dataset reads.

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load()
wdpa = catalog.resolve("wdpa-marine", format="fgb")
path = catalog.fetch("wdpa-marine", format="fgb")
```

Command line:

```bash
skytruth-datasets list
skytruth-datasets url wdpa-marine --format pmtiles
skytruth-datasets fetch wdpa-marine --format fgb
```

`Catalog.load()` reads the public bucket catalog by default and falls back to the
packaged snapshot when the public URL is unavailable. Pass a local path, `gs://`
URI, HTTPS URL, or `"packaged"` as `source` when callers need a specific catalog.
