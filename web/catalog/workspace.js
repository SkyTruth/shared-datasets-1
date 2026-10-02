import {validateSnapshot, parseSnapshotJson, SNAPSHOT_MAX_BYTES} from './workspace-contract.js';
import {captureArtifact, artifactGeneration, artifactUrl, metadataFile, releaseFile, selectReleaseReference, assertArtifactResponse} from './release-reference.js';
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
export async function prepareWorkspace(snapshot, assets, {bucket = 'skytruth-shared-datasets-1', fetchImpl = fetch} = {}) {
  snapshot = validateSnapshot(snapshot, {bucket});
  const references = [];
  for (const dataset of snapshot.datasets) {
    const current = assets.find(a => a.slug === dataset.asset_slug);
    if (!current) throw new Error(`${dataset.asset_slug}: asset is absent from the authorized catalog.`);
    if (!dataset.release) throw new Error(`${dataset.asset_slug}: this viewer cannot restore unindexed legacy identities. Python can fetch their exact bytes.`);
    const root = current.canonical_path.split(/\/(?:latest|releases)\//)[0];
    if (dataset.artifacts.some(a => !a.gs_uri.startsWith(`${root}/releases/${dataset.release}/`))) throw new Error(`${dataset.asset_slug}: artifact is outside the authorized catalog asset root.`);
    const files = dataset.artifacts.map(a => ({format: a.format, role: a.format, path: a.gs_uri, generation: a.generation, ...(a.size !== null ? {size: a.size} : {}), ...(a.sha256 !== null ? {sha256: a.sha256} : {}), ...(a.resolved_locale ? {locale: a.resolved_locale} : {})}));
    const reference = selectReleaseReference({...current, ...dataset.provenance, release_error: undefined,
      canonical_format: dataset.canonical_format, feature_identity: dataset.provenance.identity_contract,
      has_pmtiles: files.some(f => f.format === 'pmtiles'), versions: [{date: dataset.release, files}], latest_release: {date: dataset.release}}, dataset.release, {bucket});
    for (const artifact of dataset.artifacts) {
      try {
        let url;
        if (current.access_tier === 'public') url = artifactUrl({path: artifact.gs_uri, generation: artifact.generation}, {bucket});
        else {
          const params = new URLSearchParams({slug: dataset.asset_slug, version: dataset.release, generation: artifact.generation});
          const tiles = artifact.format === 'pmtiles';
          if (!tiles) { params.set('format', artifact.format); if (artifact.resolved_locale) params.set('locale', artifact.resolved_locale); }
          const response = await fetchImpl(`${tiles ? '/api/pmtiles/signed-url' : '/api/download-url'}?${params}`, {credentials: 'include', cache: 'no-store'});
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.error || `access route returned HTTP ${response.status}`);
          assertArtifactResponse(payload, reference, {path: artifact.gs_uri, generation: artifact.generation});
          url = tiles ? payload.pmtiles_url : payload.download_url;
          if (!url) throw new Error('Access route did not return an artifact URL');
        }
        const response = await fetchImpl(url, {method: 'HEAD', credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) throw new Error(`HTTP ${response.status}; captured generation unavailable`);
        const generation = response.headers.get('x-goog-generation'), size = response.headers.get('x-goog-stored-content-length') ?? response.headers.get('content-length');
        if (generation !== null && generation !== artifact.generation) throw new Error('generation mismatch');
        if (artifact.size !== null && size !== null && Number(size) !== artifact.size) throw new Error('published size mismatch');
      } catch (error) { throw new Error(`${artifact.gs_uri}#${artifact.generation}: ${error.message}. Workspace was not restored; no release was substituted.`); }
    }
    references.push(reference);
  }
  return {snapshot, references};
}
