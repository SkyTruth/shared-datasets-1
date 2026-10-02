import maplibregl, {type LayerSpecification, type MapOptions} from 'maplibre-gl';
import {PMTiles, Protocol} from 'pmtiles';
import {DEFAULT_SHARED_DATASETS_BUCKET} from './artifact-url.js';
import {resolveSnapshotLayer, fetchSnapshotMetadata,
  type SnapshotArtifact, type SnapshotDataset, type SnapshotLayerOptions, type WorkspaceSnapshot} from './snapshot.js';

export type DatasetMapReference = {tiles: string; metadata?: string | null};
export type DatasetMapOptions = SnapshotLayerOptions & {
  access?: SnapshotDataset['access_tier'];
  authorizationUrl?: string;
  mapOptions?: Omit<MapOptions, 'container' | 'style'>;
};

function parseReference(reference: string, role: 'canonical' | 'metadata'): {slug: string; release: string; artifact: SnapshotArtifact} {
  const match = /^(gs:\/\/[^#]+\/([^/]+)\/releases\/(\d{4}-\d{2}-\d{2})\/[^/]+)#([1-9][0-9]*)$/.exec(reference);
  if (!match) throw new Error('Dataset reference must be a dated gs:// URI with #generation');
  const [, gs_uri, slug, release, generation] = match;
  const locale = role === 'metadata' ? /\.metadata\.([a-z]{2,3}(?:_[a-z0-9]{2,8})*)\.ndjson\.gz$/.exec(gs_uri)?.[1] ?? null : null;
  return {slug, release, artifact: {gs_uri, generation, role, format: role === 'metadata' ? 'metadata' : 'pmtiles',
    size: null, sha256: null, requested_locale: locale, resolved_locale: locale}};
}

/** Resolve two exact objects without consulting a mutable catalog or release index. */
export async function resolveDatasetMap(reference: DatasetMapReference, options: DatasetMapOptions = {}) {
  const tiles = parseReference(reference.tiles, 'canonical');
  const metadata = reference.metadata ? parseReference(reference.metadata, 'metadata') : null;
  if (metadata && (metadata.slug !== tiles.slug || metadata.release !== tiles.release)) throw new Error('Tiles and metadata must belong to the same dataset release');
  const bucket = options.bucket ?? DEFAULT_SHARED_DATASETS_BUCKET;
  const snapshot: WorkspaceSnapshot = {kind: 'skytruth-workspace', schema_version: 1, bucket, presentation: null,
    datasets: [{asset_slug: tiles.slug, release: tiles.release, canonical_format: 'pmtiles', access_tier: options.access ?? 'public',
      artifacts: [tiles.artifact, ...(metadata ? [metadata.artifact] : [])],
      provenance: {title: null, citation: null, source: null, source_url: null, license: null, notes: null, status: null,
        lifecycle_reason: null, lifecycle_date: null, successor_asset_slug: null, consumer_guidance: null, source_version: null,
        release_index_uri: null, release_index_generation: null, release_index_updated_at: null, identity_contract: null}}]};
  const authorizeArtifact = options.authorizeArtifact ?? (options.authorizationUrl ? async (dataset: SnapshotDataset, artifact: SnapshotArtifact) => {
    const response = await fetch(options.authorizationUrl!, {method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({snapshot, asset_slug: dataset.asset_slug, role: artifact.role, locale: artifact.resolved_locale})});
    if (!response.ok) throw new Error(`Dataset access failed: HTTP ${response.status}`);
    return response.json();
  } : undefined);
  const layer = await resolveSnapshotLayer(snapshot, tiles.slug, {...options, bucket, authorizeArtifact});
  const records = await fetchSnapshotMetadata(layer);
  return {layer, records};
}

let protocol: Protocol | null = null;
/** Create a styled MapLibre map, fit its extent, and show matching metadata on click. */
export async function showDataset(container: string | HTMLElement, reference: DatasetMapReference, options: DatasetMapOptions = {}) {
  const {layer, records} = await resolveDatasetMap(reference, options);
  const archive = new PMTiles(layer.tileUrl);
  const [metadata, header] = await Promise.all([archive.getMetadata(), archive.getHeader()]);
  const vectorLayers = metadata && typeof metadata === 'object' && 'vector_layers' in metadata ? metadata.vector_layers : null;
  if (!Array.isArray(vectorLayers) || !vectorLayers.length || !vectorLayers.every(item => item && typeof item.id === 'string')) throw new Error('This dataset requires vector PMTiles with named layers');
  if (!protocol) { protocol = new Protocol(); maplibregl.addProtocol('pmtiles', protocol.tile); }
  protocol.add(archive);
  const layers = vectorLayers.flatMap<LayerSpecification>(({id}, index) => [
    {id: `fill-${index}`, type: 'fill', source: 'dataset', 'source-layer': id, filter: ['==', ['geometry-type'], 'Polygon'], paint: {'fill-color': '#1f7a59', 'fill-opacity': 0.35}},
    {id: `line-${index}`, type: 'line', source: 'dataset', 'source-layer': id, filter: ['!=', ['geometry-type'], 'Point'], paint: {'line-color': '#1f7a59', 'line-width': 2}},
    {id: `point-${index}`, type: 'circle', source: 'dataset', 'source-layer': id, filter: ['==', ['geometry-type'], 'Point'], paint: {'circle-color': '#1f7a59', 'circle-radius': 5}},
  ]);
  const map = new maplibregl.Map({...options.mapOptions, container,
    style: {version: 8, sources: {dataset: {type: 'vector', url: `pmtiles://${layer.tileUrl}`}},
      layers: [{id: 'background', type: 'background', paint: {'background-color': '#f7faf8'}}, ...layers]}});
  map.once('load', () => map.fitBounds([[header.minLon, header.minLat], [header.maxLon, header.maxLat]], {padding: 34, maxZoom: 8, duration: 0}));
  map.on('click', event => {
    const hit = map.queryRenderedFeatures(event.point, {layers: layers.map(item => item.id)})[0];
    if (!hit) return;
    const properties = records.get(String(hit.properties?.feature_id ?? ''))?.properties ?? hit.properties;
    const content = document.createElement('dl');
    for (const [key, value] of Object.entries(properties ?? {})) {
      const label = document.createElement('dt'), text = document.createElement('dd');
      label.textContent = key; text.textContent = typeof value === 'object' ? JSON.stringify(value) : String(value);
      content.append(label, text);
    }
    new maplibregl.Popup().setLngLat(event.lngLat).setDOMContent(content).addTo(map);
  });
  return {map, layer, records};
}
