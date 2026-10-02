import {validateSnapshot, parseSnapshotJson, SNAPSHOT_MAX_BYTES} from './workspace-contract.js';
import {captureArtifact, artifactGeneration, metadataFile, releaseFile} from './release-reference.js';
export {validateSnapshot, parseSnapshotJson, SNAPSHOT_MAX_BYTES};
const identityKeys = ['strategy', 'source_fields', 'generated_id_type', 'assignment_key', 'feature_id_column', 'geometry_hash_column', 'properties_hash_column'];
const metadataKeys = ['title', 'citation', 'source', 'source_url', 'license', 'notes', 'status', 'lifecycle_reason', 'lifecycle_date', 'successor_asset_slug', 'consumer_guidance', 'source_version'];
export function captureWorkspace(references, {bucket = 'skytruth-shared-datasets-1', presentation = null, locale = null} = {}) {
  const datasets = references.map(reference => {
    if (reference.release_error) throw new Error(reference.release_error);
    if (!reference.release_snapshot_key || !reference.date) throw new Error(`${reference.slug}: release-index artifact identities are missing. Ordinary browsing remains available; this selection is not reproducibly pinned.`);
    const canonical = reference.canonical_file;
    const artifacts = [captureArtifact(canonical, 'canonical')];
    if (reference.pmtiles_file && reference.pmtiles_file.path !== canonical.path) artifacts.push(captureArtifact(reference.pmtiles_file, 'tiles'));
    const sourceMetadata = metadataFile(reference.files, '');
    const selectedMetadata = metadataFile(reference.files, locale || '');
    if (sourceMetadata) artifacts.push(captureArtifact(sourceMetadata, 'metadata', selectedMetadata?.path === sourceMetadata.path ? locale : null));
    if (selectedMetadata && selectedMetadata.path !== sourceMetadata?.path) artifacts.push(captureArtifact(selectedMetadata, 'metadata', locale));
    const schema = releaseFile(reference.files, 'schema');
    if (schema) artifacts.push(captureArtifact(schema, 'schema'));
    const provenance = Object.fromEntries(metadataKeys.map(key => [key, reference[key] || null]));
    provenance.release_index_uri = `gs://${bucket}/_catalog/releases/${reference.slug}.json`;
    provenance.release_index_generation = reference.release_index_generation ? artifactGeneration(reference.release_index_generation) : null;
    provenance.release_index_updated_at = reference.release_index_updated_at || null;
    provenance.identity_contract = reference.feature_identity ? Object.fromEntries(Object.entries(reference.feature_identity).filter(([key]) => identityKeys.includes(key))) : null;
    return {asset_slug: reference.slug, release: reference.date, canonical_format: reference.canonical_format,
      access_tier: reference.access_tier, artifacts, provenance};
  });
  return validateSnapshot({kind: 'skytruth-workspace', schema_version: 1, bucket, datasets, presentation}, {bucket});
}
export function attribution(snapshot) {
  return snapshot.datasets.map(d => `Data: ${d.provenance.source || d.provenance.title || d.asset_slug} · SkyTruth`).join('; ');
}
function pinnedUri(artifact) { return `${artifact.gs_uri}#${artifact.generation}`; }
export function pythonSnippet(snapshot) {
  const dataset = snapshot.datasets[0], artifact = dataset.artifacts.find(a => a.role === 'canonical');
  const bucket = snapshot.bucket === 'skytruth-shared-datasets-1' ? '' : `, bucket=${JSON.stringify(snapshot.bucket)}`;
  const fetch = `path = fetch_artifact(${JSON.stringify(pinnedUri(artifact))}${bucket})`;
  if (artifact.format === 'pmtiles') return ['from skytruth_shared_datasets import fetch_artifact', fetch, 'print(path)'].join('\n');
  const examples = {
    csv: ['import csv', 'data = list(csv.DictReader(path.open(encoding="utf-8", newline="")))'],
    geojson: ['import json', 'data = json.loads(path.read_text(encoding="utf-8"))'],
    ndgeojson: ['import json', 'with path.open(encoding="utf-8") as source:', '    data = [json.loads(line) for line in source]'],
    fgb: ['import geopandas as gpd', 'data = gpd.read_file(path)'],
    cog: ['import rasterio', 'with rasterio.open(path) as raster:', '    data = raster.read()'],
  };
  const [dependency, ...consumer] = examples[artifact.format];
  return [dependency, 'from skytruth_shared_datasets import fetch_artifact', fetch, ...consumer].join('\n');
}
export function typescriptSnippet(snapshot) {
  const dataset = snapshot.datasets[0];
  const tiles = dataset.artifacts.find(a => a.format === 'pmtiles');
  if (!tiles) return null;
  const metadata = dataset.artifacts.find(a => a.role === 'metadata' && a.requested_locale !== null)
    || dataset.artifacts.find(a => a.role === 'metadata');
  const options = [];
  if (snapshot.bucket !== 'skytruth-shared-datasets-1') options.push(`bucket: ${JSON.stringify(snapshot.bucket)}`);
  if (dataset.access_tier !== 'public') options.push(`access: ${JSON.stringify(dataset.access_tier)}, authorizationUrl: "/api/snapshot-artifact"`);
  return [
    'import {showDataset} from "@skytruth/shared-datasets/maplibre";',
    'const dataset = await showDataset("map", {',
    `  tiles: ${JSON.stringify(pinnedUri(tiles))},`,
    `  metadata: ${metadata ? JSON.stringify(pinnedUri(metadata)) : 'null'},`,
    `}${options.length ? `, {${options.join(', ')}}` : ''});`,
  ].join('\n');
}
