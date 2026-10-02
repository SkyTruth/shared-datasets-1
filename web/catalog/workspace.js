import {validateSnapshot, parseSnapshotJson, SNAPSHOT_MAX_BYTES} from './workspace-contract.js';
import {artifactGeneration, artifactUrl, metadataFile, releaseFile, selectReleaseReference, assertArtifactResponse} from './release-reference.js';
export {validateSnapshot, parseSnapshotJson, SNAPSHOT_MAX_BYTES};
const identityKeys = ['strategy', 'source_fields', 'generated_id_type', 'assignment_key', 'feature_id_column', 'geometry_hash_column', 'properties_hash_column'];
const metadataKeys = ['title', 'citation', 'source', 'source_url', 'license', 'notes', 'status', 'lifecycle_reason', 'lifecycle_date', 'successor_asset_slug', 'consumer_guidance', 'source_version'];
function captureArtifact(file, role, requestedLocale = null) {
  if (!file) throw new Error(`Exact ${role} identity is missing from the release index.`);
  const resolvedLocale = role === 'metadata' ? (/\.metadata\.([a-z]{2,3}(?:_[a-z0-9]{2,8})*)\.ndjson\.gz$/.exec(file.path)?.[1] || null) : null;
  return {format: file.format, role, gs_uri: file.path, generation: artifactGeneration(file.generation),
    size: file.size ?? null, sha256: file.sha256?.toLowerCase() ?? null,
    requested_locale: requestedLocale, resolved_locale: resolvedLocale};
}
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
  return snapshot.datasets.map(d => `${d.provenance.title || d.asset_slug} (${d.asset_slug}, release ${d.release || 'unknown'}). ${d.provenance.citation || 'Citation unknown.'}\nSource: ${d.provenance.source || 'Unknown'}${d.provenance.source_url ? ` — ${d.provenance.source_url}` : ''}\nTerms: ${d.provenance.license || 'Unknown'}\n${[d.provenance.notes, d.provenance.lifecycle_reason, d.provenance.consumer_guidance].filter(Boolean).join('\n')}`).join('\n\n');
}
function pythonLiteral(value) { return JSON.stringify(JSON.stringify(value)); }
export function pythonSnippet(snapshot) {
  const dataset = snapshot.datasets[0], artifact = dataset.artifacts.find(a => a.role === 'canonical');
  const examples = {
    csv: 'import csv\nwith path.open(newline="", encoding="utf-8") as source:\n    print(next(csv.DictReader(source), None))',
    geojson: 'data = json.loads(path.read_text(encoding="utf-8"))\nprint(len(data.get("features", [])))',
    ndgeojson: 'with path.open(encoding="utf-8") as source:\n    print(json.loads(next(source)))',
    fgb: 'import geopandas as gpd  # uv pip install geopandas\nfeatures = gpd.read_file(path)\nprint(features.head())',
    cog: 'import rasterio  # uv pip install rasterio\nwith rasterio.open(path) as raster:\n    print(raster.bounds, raster.count)',
    pmtiles: 'with path.open("rb") as source:\n    print(source.read(8))  # PMTiles archive header',
  };
  return `import json\nfrom pathlib import Path\nfrom skytruth_shared_datasets import fetch_snapshot_artifact\n\nlock = json.loads(${pythonLiteral(snapshot)})\n# ADC from your runtime identity, or gcloud auth application-default login locally.\nfetched = fetch_snapshot_artifact(lock, ${JSON.stringify(dataset.asset_slug)}, bucket=${JSON.stringify(snapshot.bucket)})\npath = fetched.cache_path\nlineage = fetched.lineage()\nPath("dataset-lineage.json").write_text(json.dumps(lineage, indent=2))\nprint(path, lineage)\n\n${examples[artifact.format]}\n`;
}
export function typescriptSnippet(snapshot) {
  const dataset = snapshot.datasets[0];
  if (!dataset.artifacts.some(a => a.format === 'pmtiles')) return 'This selection does not publish PMTiles. Use the Python exact-fetch example.';
  const restricted = dataset.access_tier !== 'public';
  return `import maplibregl from "maplibre-gl";\nimport "maplibre-gl/dist/maplibre-gl.css";\nimport {Protocol, PMTiles} from "pmtiles";\nimport {validateWorkspaceSnapshot, resolveSnapshotLayer, fetchSnapshotMetadata} from "@skytruth/shared-datasets";\n\nconst lock = validateWorkspaceSnapshot(${JSON.stringify(snapshot, null, 2)}, {bucket: ${JSON.stringify(snapshot.bucket)}});\n${restricted ? '// Integration requires your application’s authenticated POST /api/snapshot-artifact route.\n// Server: authorizeSnapshotArtifact from @skytruth/shared-datasets/server.\n// Recheck catalog tier, entitlement, release root, and indexed URI/generation; never sign arbitrary paths.\n' : '// Complete public data integration. Provide a <div id="map" style="height: 500px"></div>.\n'}const layer = await resolveSnapshotLayer(lock, ${JSON.stringify(dataset.asset_slug)}, {bucket: lock.bucket${restricted ? ',\n  authorizeArtifact: async (dataset, artifact) => {\n    const response = await fetch("/api/snapshot-artifact", {method: "POST", credentials: "include",\n      headers: {"Content-Type": "application/json"},\n      body: JSON.stringify({snapshot: lock, asset_slug: dataset.asset_slug, role: artifact.role, locale: artifact.resolved_locale})});\n    if (!response.ok) throw new Error(`Snapshot access failed: HTTP ${response.status}`);\n    return response.json();\n  }' : ''}});\nconst records = await fetchSnapshotMetadata(layer);\nconst protocol = new Protocol();\nmaplibregl.addProtocol("pmtiles", protocol.tile);\nconst archive = new PMTiles(layer.tileUrl);\nprotocol.add(archive);\nconst metadata = await archive.getMetadata();\nconst header = await archive.getHeader();\nconst sourceLayers = metadata && typeof metadata === "object" && "vector_layers" in metadata ? metadata.vector_layers : null;\nif (!Array.isArray(sourceLayers) || !sourceLayers.length || !sourceLayers.every(layer => layer && typeof layer.id === "string")) throw new Error("This example requires vector PMTiles");\nconst layers = sourceLayers.flatMap<maplibregl.LayerSpecification>(({id}, index) => [\n  {id: "fill-" + index, type: "fill", source: "dataset", "source-layer": id, filter: ["==", ["geometry-type"], "Polygon"], paint: {"fill-color": "#1f7a59", "fill-opacity": 0.35}},\n  {id: "line-" + index, type: "line", source: "dataset", "source-layer": id, filter: ["!=", ["geometry-type"], "Point"], paint: {"line-color": "#1f7a59", "line-width": 2}},\n  {id: "point-" + index, type: "circle", source: "dataset", "source-layer": id, filter: ["==", ["geometry-type"], "Point"], paint: {"circle-color": "#1f7a59", "circle-radius": 5}}\n]);\nconst map = new maplibregl.Map({container: "map", center: [0, 15], zoom: 1,\n  style: {version: 8, sources: {dataset: {type: "vector", url: "pmtiles://" + layer.tileUrl}}, layers: [{id: "background", type: "background", paint: {"background-color": "#f7faf8"}}, ...layers]}});\nmap.once("load", () => map.fitBounds([[header.minLon, header.minLat], [header.maxLon, header.maxLat]], {padding: 34, maxZoom: 8, duration: 0}));\nmap.on("click", event => {\n  const hit = map.queryRenderedFeatures(event.point)[0];\n  const featureId = String(hit?.properties?.feature_id || "");\n  console.log(records.get(featureId)?.properties, {citation: layer.dataset.provenance.citation, accessTier: layer.dataset.access_tier});\n});\n`;
}
export function installationInstructions(revision) {
  if (revision && /^[a-f0-9]{40}$/.test(revision)) return `uv pip install "skytruth-shared-datasets[gcs] @ https://github.com/SkyTruth/shared-datasets-1/archive/${revision}.zip#subdirectory=api/python"\n\nTypeScript: check out the same repository revision (${revision}), then run:\nnpm ci --prefix api/typescript\n\nThen, in your app (adjust the checkout path):\nnpm install /path/to/shared-datasets-1/api/typescript maplibre-gl@5.9.0 pmtiles@4.3.0`;
  return 'Unreleased local build: from this repository checkout, run:\nuv pip install -e "api/python[gcs]"\nnpm ci --prefix api/typescript\n\nThen, in your app (adjust the checkout path):\nnpm install /path/to/shared-datasets-1/api/typescript maplibre-gl@5.9.0 pmtiles@4.3.0\n\nA deployed build must provide its reviewed 40-character SDK revision. The published npm version does not yet include these APIs.';
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
