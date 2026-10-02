import {validateWorkspaceSnapshot, type WorkspaceSnapshot, type SnapshotArtifact, type AuthorizedSnapshotArtifact} from './snapshot.js';
import {isViewerAuthorizedForTier, getViewerTierAuthorization, type PmtilesViewer, type PmtilesAccessPolicy} from './private-access.js';
import {getSignedSharedDatasetArtifactUrl, type SharedDatasetArtifactSignedUrlConfig} from './artifact-url-server.js';
import {sharedDatasetArtifactUrlFromGsUri, resolveSharedDatasetRelease, normalizeArtifactGeneration, type SharedDatasetReleaseIndex} from './artifact-url.js';

export type SnapshotAuthorizationOptions = {
  bucket?: string; viewer: PmtilesViewer | null; policy?: Partial<PmtilesAccessPolicy>;
  getAsset: (slug: string) => Promise<{access_tier: 'public' | 'private' | 'internal'; canonical_path: string}>;
  getReleaseIndex: (slug: string) => Promise<SharedDatasetReleaseIndex>;
  getSigningKey: () => Promise<Buffer>;
  signingConfig?: SharedDatasetArtifactSignedUrlConfig;
};
/** Server route primitive. The imported URI never becomes an unrestricted signing request.
 * Authorize the current catalog tier and require the exact file/generation to remain indexed.
 * Older replaced generations fail even when GCS still retains them.
 */
export async function authorizeSnapshotArtifact(input: unknown, slug: string, role: SnapshotArtifact['role'], locale: string | null, options: SnapshotAuthorizationOptions): Promise<AuthorizedSnapshotArtifact> {
  const snapshot: WorkspaceSnapshot = validateWorkspaceSnapshot(input, {bucket: options.bucket});
  const dataset = snapshot.datasets.find(d => d.asset_slug === slug);
  const artifact = dataset?.artifacts.find(a => a.role === role && a.resolved_locale === locale);
  if (!dataset || !artifact) throw new Error('Captured artifact is absent');
  const asset = await options.getAsset(slug);
  if (!isViewerAuthorizedForTier(asset.access_tier, options.viewer, options.policy)) throw new Error('Captured artifact is not authorized');
  if (!dataset.release) throw new Error('Viewer cannot authorize unindexed legacy generations');
  const root = asset.canonical_path.split(/\/(?:latest|releases)\//)[0];
  if (!artifact.gs_uri.startsWith(`${root}/releases/${dataset.release}/`)) throw new Error('Captured artifact is outside the catalog asset root');
  const index = await options.getReleaseIndex(slug);
  if (index.asset_slug !== slug) throw new Error('Release index identity mismatch');
  const release = resolveSharedDatasetRelease(index, dataset.release);
  const file = release.files?.find(f => f.path === artifact.gs_uri && normalizeArtifactGeneration(f.generation) === artifact.generation && f.format === artifact.format);
  if (!file) throw new Error('Viewer cannot authorize this captured older generation; reselecting latest would change the workspace');
  if ((file.sha256 != null && artifact.sha256 != null && String(file.sha256).toLowerCase() !== artifact.sha256) || (file.size != null && artifact.size != null && file.size !== artifact.size)) throw new Error('Captured integrity expectations disagree with the indexed artifact');
  let url: string;
  if (asset.access_tier === 'public') {
    url = sharedDatasetArtifactUrlFromGsUri(artifact.gs_uri, {bucketName: options.bucket, generation: artifact.generation});
  } else {
    const authorization = getViewerTierAuthorization(asset.access_tier, options.viewer, options.policy);
    const config = {...options.signingConfig, bucketName: options.bucket, generation: artifact.generation};
    if (authorization.expiresAt) {
      const remaining = Math.floor((authorization.expiresAt.getTime() - (config.now ?? Date.now)()) / 1000);
      if (remaining <= 0) throw new Error('Snapshot authorization has expired');
      config.ttlSeconds = Math.min(config.ttlSeconds ?? 900, remaining);
    }
    url = getSignedSharedDatasetArtifactUrl(artifact.gs_uri, await options.getSigningKey(), config);
  }
  return {gs_uri: artifact.gs_uri, generation: artifact.generation, resolved_release: dataset.release, url};
}
