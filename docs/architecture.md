# Shared dataset runtime boundaries

The repository is the control plane; Cloud Storage holds the dataset bytes and
publication records. Consumer SDKs resolve exact artifacts without depending on
the ingestion packages or command-line tools.

## Release rules and adapters

`ingestion/common/release_feature_model.py` owns feature IDs, content hashes,
release schemas, manifests and identity decisions. It has standard-library
imports only. Both ingestion jobs and `scripts/` import this module; it never
imports a CLI, service or job. The model's public serialized formats are
unchanged by its move from `scripts/`.

`ingestion/common/lifecycle.py` owns the stage order used by MPAtlas, EAMLIS and
sea ice. Job adapters own their source normalization and domain policies. WDPA
keeps its separate post-build fingerprint orchestration. Native artifact tools
remain in their existing builder modules.

## Publication

`ingestion/common/publication.py` owns immutable intents, generation expectations,
asset claims and the recovery journal. `publication_bundle.py` owns the shared
checkpoint/data/manifest/run/index recipe and its versioned JSON derivations.
Generated-ID and source-field adapters validate their distinct identity rules
before using that same recipe and executor.

The MPAtlas source-field adapter adopts a verified, generation-pinned baseline
under its source-quality guard, without rewriting data or changing feature IDs.
It stages every validated input before persisting a transaction intent. Once an
intent exists, a retry uses the captured input generations and original
preconditions; it does not download upstream data or rebuild local artifacts.
A foreign destination change fails rather than refreshing its expectation.
Reviewed source-quality promotions refuse an active transaction and reconcile
managed baseline anchors only after complete approved bytes are verified.

## Validation evidence

CI fingerprints workflow/action definitions, pinned toolchain/dependency inputs,
validator entrypoints and their discovered imports and literal program inputs.
This includes installed probe programs passed as bytes. An unrelated CLI does
not redefine the validation protocol. Ordinary application changes still select
checks through the existing dependency graph; unknown inputs remain broad.
Local reuse does not grant hosted validation or deployment authority.

## Consumers

The TypeScript metadata reader bounds compressed bytes, expanded bytes and record
count, verifies captured size/checksum expectations, and decodes NDJSON in chunks.
The map helper creates the map before loading attributes; its resolved return
value still contains all successfully loaded records. Removing the map cancels
its metadata read. Server lookups use a bounded cache and coordinate cold reads
per immutable sidecar, allowing unrelated cached lookups during network IO.
