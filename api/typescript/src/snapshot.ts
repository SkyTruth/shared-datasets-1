import {parseSnapshotJson, validateSnapshot} from './snapshot-contract.js';
import {sharedDatasetArtifactUrlFromGsUri, DEFAULT_SHARED_DATASETS_BUCKET} from './artifact-url.js';
import {parseSharedDatasetMetadataRecords, type SharedDatasetMetadataRecord} from './metadata-records.js';

export type SnapshotArtifact = {
  format: 'fgb' | 'csv' | 'geojson' | 'ndgeojson' | 'cog' | 'pmtiles' | 'metadata' | 'schema';
  role: 'canonical' | 'tiles' | 'metadata' | 'schema';
  gs_uri: string; generation: string; size: number | null; sha256: string | null;
  requested_locale: string | null; resolved_locale: string | null;
};
export type SnapshotDataset = {
  asset_slug: string; release: string | null; canonical_format: SnapshotArtifact['format'];
  access_tier: 'public' | 'private' | 'internal'; artifacts: SnapshotArtifact[];
  provenance: {title: string | null; citation: string | null; source: string | null;
    source_url: string | null; license: string | null; notes: string | null; status: string | null;
    lifecycle_reason: string | null; lifecycle_date: string | null; successor_asset_slug: string | null;
    consumer_guidance: string | null; source_version: string | null; release_index_uri: string | null; release_index_generation: string | null;
    release_index_updated_at: string | null; identity_contract: Record<string, string | string[]> | null};
};
export type WorkspaceSnapshot = {
  kind: 'skytruth-workspace'; schema_version: 1; bucket: string; datasets: SnapshotDataset[];
  presentation: null | {basemap: 'map' | 'satellite'; locale: string | null;
    viewport: null | {center: [number, number]; zoom: number; bearing: number; pitch: number};
    layers: {asset_slug: string; visible: true; source_layer: string | null; color_field: string | null}[]};
};
export function validateWorkspaceSnapshot(input: unknown, {bucket = DEFAULT_SHARED_DATASETS_BUCKET} = {}): WorkspaceSnapshot {
  return typeof input === 'string' ? parseSnapshotJson(input, {bucket}) : validateSnapshot(input, {bucket});
}
export type AuthorizedSnapshotArtifact = {gs_uri: string; generation: string; resolved_release: string | null; url: string};
export type SnapshotLayerOptions = {
  bucket?: string; artifactBaseUrl?: string;
  authorizeArtifact?: (dataset: SnapshotDataset, artifact: SnapshotArtifact) => Promise<AuthorizedSnapshotArtifact>;
  probeArtifact?: (url: string, artifact: SnapshotArtifact) => Promise<void>;
};
export type SnapshotLayer = {
  dataset: SnapshotDataset; tileUrl: string;
  metadata: {artifact: SnapshotArtifact; url: string} | null;
  artifacts: {artifact: SnapshotArtifact; url: string}[];
};
export function assertSnapshotAuthorization(payload: AuthorizedSnapshotArtifact, dataset: SnapshotDataset, artifact: SnapshotArtifact): void {
  if (payload.gs_uri !== artifact.gs_uri || payload.generation !== artifact.generation || payload.resolved_release !== dataset.release || !payload.url) throw new Error('Authorized artifact does not match the captured identity');
  const url = new URL(payload.url, globalThis.location?.href);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Authorized artifact URL must use HTTP');
}
const probeArtifact = async (url: string, artifact: SnapshotArtifact) => {
  const response = await fetch(url, {method: 'HEAD', credentials: 'same-origin', cache: 'no-store'});
  if (!response.ok) throw new Error(`${artifact.gs_uri}#${artifact.generation}: HTTP ${response.status}; captured bytes unavailable`);
  const generation = response.headers.get('x-goog-generation');
  if (generation !== null && generation !== artifact.generation) throw new Error('Artifact generation mismatch');
  const size = response.headers.get('x-goog-stored-content-length') ?? response.headers.get('content-length');
  if (size !== null && artifact.size !== null && Number(size) !== artifact.size) throw new Error('Artifact size mismatch');
};
/** Validate and establish access/availability for every required artifact before returning a mountable layer. */
export async function resolveSnapshotLayer(input: unknown, slug: string, options: SnapshotLayerOptions = {}): Promise<SnapshotLayer> {
  const snapshot = validateWorkspaceSnapshot(input, {bucket: options.bucket});
  const dataset = snapshot.datasets.find(d => d.asset_slug === slug);
  if (!dataset) throw new Error(`Dataset ${slug} is absent from the snapshot`);
  const artifacts: SnapshotLayer['artifacts'] = [];
  for (const artifact of dataset.artifacts) {
    let url: string;
    if (dataset.access_tier === 'public') {
      url = sharedDatasetArtifactUrlFromGsUri(artifact.gs_uri, {bucketName: options.bucket, artifactBaseUrl: options.artifactBaseUrl, generation: artifact.generation});
    } else {
      if (!options.authorizeArtifact) throw new Error('Restricted snapshot requires an app-owned authorized artifact route');
      const authorized = await options.authorizeArtifact(dataset, artifact);
      assertSnapshotAuthorization(authorized, dataset, artifact);
      url = authorized.url;
    }
    await (options.probeArtifact ?? probeArtifact)(url, artifact);
    artifacts.push({artifact, url});
  }
  const tiles = artifacts.find(entry => entry.artifact.role === 'tiles' || entry.artifact.format === 'pmtiles');
  if (!tiles) throw new Error('Captured dataset has no PMTiles');
  const requested = snapshot.presentation ? snapshot.presentation.locale : (artifacts.find(e => e.artifact.role === 'metadata' && e.artifact.requested_locale !== null)?.artifact.requested_locale ?? null);
  const metadata = artifacts.find(e => e.artifact.role === 'metadata' && e.artifact.requested_locale === requested)
    ?? artifacts.find(e => e.artifact.role === 'metadata' && e.artifact.resolved_locale === null) ?? null;
  return {dataset, tileUrl: tiles.url, metadata, artifacts};
}
/** Execute this for every layer before mounting any layers for an atomic workspace restore. */
export async function resolveSnapshotLayers(input: unknown, options: SnapshotLayerOptions = {}): Promise<SnapshotLayer[]> {
  const snapshot = validateWorkspaceSnapshot(input, {bucket: options.bucket});
  return Promise.all(snapshot.datasets.map(d => resolveSnapshotLayer(snapshot, d.asset_slug, options)));
}
export async function fetchSnapshotMetadata(layer: SnapshotLayer, {fetchBytes = async (url: string) => {
  const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
  if (!response.ok) throw new Error(`Metadata unavailable: HTTP ${response.status}`);
  return response.arrayBuffer();
}} = {}): Promise<Map<string, SharedDatasetMetadataRecord>> {
  if (!layer.metadata) return new Map();
  const {artifact, url} = layer.metadata;
  const buffer = await fetchBytes(url);
  if (artifact.size !== null && buffer.byteLength !== artifact.size) throw new Error('Metadata size mismatch');
  if (artifact.sha256 !== null) {
    const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', buffer))].map(x => x.toString(16).padStart(2, '0')).join('');
    if (hash !== artifact.sha256) throw new Error('Metadata SHA-256 mismatch');
  }
  const bytes = new Uint8Array(buffer);
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) throw new Error('Captured metadata must be gzip NDJSON');
  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'));
  const records = parseSharedDatasetMetadataRecords(await new Response(stream).text());
  for (const record of records.values()) {
    if ((record.asset_slug !== undefined && record.asset_slug !== layer.dataset.asset_slug) || (record.release !== undefined && record.release !== layer.dataset.release)) throw new Error('Metadata record identity mismatch');
  }
  return records;
}
