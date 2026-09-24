import {
  SharedDatasetCatalogResolutionError,
  getSharedDatasetAccessTier
} from './catalog.js';

export const DEFAULT_SHARED_DATASETS_BUCKET = 'skytruth-shared-datasets-1';
export const DEFAULT_SHARED_DATASETS_ARTIFACTS_URL_BASE =
  'https://tiles.skytruth.org/artifacts';

const RELEASE_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const FIELD_SAFE_LOCALE_RE = /^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$/;
const LOCALIZED_METADATA_FILE_RE =
  /\.metadata\.([a-z]{2,3}(?:_[a-z0-9]{2,8})*)\.ndjson\.gz$/;

export type SharedDatasetReleaseFile = {
  format?: string | null;
  role?: string | null;
  path?: string | null;
  locale?: string | null;
  generation?: number | string | null;
  [key: string]: unknown;
};

export type SharedDatasetRelease = {
  date?: string | null;
  files?: SharedDatasetReleaseFile[];
  [key: string]: unknown;
};

export type SharedDatasetReleaseIndex = {
  schema_version?: number;
  asset_slug?: string | null;
  latest_release?: SharedDatasetRelease | null;
  releases?: SharedDatasetRelease[];
  [key: string]: unknown;
};

export type SharedDatasetArtifactUrlOptions = {
  bucketName?: string;
  artifactBaseUrl?: string;
  generation?: string | number | null;
};

export type SharedDatasetMetadataSidecarOptions = {
  releaseIndex: SharedDatasetReleaseIndex;
  version?: string | null;
  locale?: string | null;
};

export type SharedDatasetPublicMetadataSidecarUrlOptions =
  SharedDatasetMetadataSidecarOptions &
    SharedDatasetArtifactUrlOptions & {
      accessTier?: string | null;
    };

export type SharedDatasetMetadataSidecarResolution = {
  requestedVersion: string;
  resolvedVersion: string;
  requestedLocale: string | null;
  resolvedLocale: string | null;
  metadataLocaleFallback: boolean;
  file: SharedDatasetReleaseFile;
  gsUri: string;
  generation: string;
  filename: string;
};

export type SharedDatasetPublicMetadataSidecarUrlResolution =
  SharedDatasetMetadataSidecarResolution & {
    url: string;
  };

export const normalizeSharedDatasetMetadataLocale = (
  locale: string | null | undefined
) => {
  const normalized = String(locale ?? '').trim().toLowerCase().replaceAll('-', '_');
  if (!normalized) return '';
  if (!FIELD_SAFE_LOCALE_RE.test(normalized)) {
    throw new SharedDatasetCatalogResolutionError(
      'Metadata locale must be a field-safe BCP 47 code such as es, fr, pt_br, or zh_hans'
    );
  }
  return normalized;
};

export const sharedDatasetArtifactUrlFromGsUri = (
  gsUri: string,
  {
    bucketName = DEFAULT_SHARED_DATASETS_BUCKET,
    artifactBaseUrl = DEFAULT_SHARED_DATASETS_ARTIFACTS_URL_BASE,
    generation
  }: SharedDatasetArtifactUrlOptions = {}
) => {
  const match = String(gsUri || '').match(/^gs:\/\/([^/]+)\/(.+)$/);
  if (!match) {
    throw new SharedDatasetCatalogResolutionError(
      'Shared dataset artifact path must be a gs:// URI'
    );
  }
  const [, bucket, objectName] = match;
  if (bucket !== bucketName) {
    throw new SharedDatasetCatalogResolutionError(
      `Shared dataset artifact path must be in gs://${bucketName}/`
    );
  }
  const segments = objectName.split('/');
  if (
    !segments.length ||
    segments.some(segment => !segment || segment === '.' || segment === '..')
  ) {
    throw new SharedDatasetCatalogResolutionError(
      'Shared dataset artifact object path is invalid'
    );
  }
  const encodedObjectPath = segments.map(encodeURIComponent).join('/');
  return `${artifactBaseUrl.replace(/\/+$/, '')}/${encodedObjectPath}${generation == null ? '' : `?generation=${normalizeArtifactGeneration(generation)}`}`;
};

export const resolveSharedDatasetMetadataSidecar = ({
  releaseIndex,
  version = 'latest',
  locale = ''
}: SharedDatasetMetadataSidecarOptions) => {
  const requestedVersion = String(version || 'latest').trim();
  if (requestedVersion !== 'latest' && !RELEASE_DATE_RE.test(requestedVersion)) {
    throw new SharedDatasetCatalogResolutionError(
      'Metadata version must be latest or YYYY-MM-DD'
    );
  }
  const requestedLocale = normalizeSharedDatasetMetadataLocale(locale);
  const release = resolveSharedDatasetRelease(releaseIndex, requestedVersion);

  const file = metadataFileForLocale(release.files, requestedLocale);
  if (!file) return null;

  const gsUri = String(file.path || '').trim();
  validateReleaseArtifact(file, releaseIndex.asset_slug || '', String(release.date));
  const generation = normalizeArtifactGeneration(file.generation);
  const resolvedLocale = metadataLocaleForFile(file);
  return {
    requestedVersion,
    resolvedVersion: String(release.date || requestedVersion),
    requestedLocale: requestedLocale || null,
    resolvedLocale: resolvedLocale || null,
    metadataLocaleFallback: Boolean(requestedLocale && requestedLocale !== resolvedLocale),
    file,
    gsUri,
    generation,
    filename: gsUri.split('/').filter(Boolean).pop() || ''
  } satisfies SharedDatasetMetadataSidecarResolution;
};

export const resolvePublicSharedDatasetMetadataSidecarUrl = (
  options: SharedDatasetPublicMetadataSidecarUrlOptions
) => {
  const accessTier = getSharedDatasetAccessTier(options.accessTier || 'public');
  if (accessTier !== 'public') {
    throw new SharedDatasetCatalogResolutionError(
      'Public metadata sidecar URLs can only be resolved for public assets'
    );
  }
  const sidecar = resolveSharedDatasetMetadataSidecar(options);
  if (!sidecar) return null;
  return {
    ...sidecar,
    url: sharedDatasetArtifactUrlFromGsUri(sidecar.gsUri, {...options, generation: sidecar.generation})
  } satisfies SharedDatasetPublicMetadataSidecarUrlResolution;
};

export const normalizeArtifactGeneration = (value: unknown): string => {
  const text = typeof value === 'number' && Number.isSafeInteger(value) ? String(value) : value;
  if (typeof text !== 'string' || !/^[1-9][0-9]{0,19}$/.test(text) || BigInt(text) > 18446744073709551615n) {
    throw new SharedDatasetCatalogResolutionError('Artifact generation must be an exact positive 64-bit integer; legacy unpinned artifacts cannot form an exact layer');
  }
  return text;
};

export const resolveSharedDatasetRelease = (index: SharedDatasetReleaseIndex, version: string = 'latest'): SharedDatasetRelease => {
  const validDate = (value: unknown) => typeof value === 'string' && RELEASE_DATE_RE.test(value) && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  if (version !== 'latest' && !validDate(version)) throw new SharedDatasetCatalogResolutionError('Version must be latest or YYYY-MM-DD');
  if (index.schema_version !== 1 || !Array.isArray(index.releases)) throw new SharedDatasetCatalogResolutionError('Invalid release index schema or releases');
  const dates = new Set<string>();
  for (const release of index.releases) {
    if (!isRelease(release) || !validDate(release.date) || !Array.isArray(release.files) || release.files.some(f => !f || typeof f !== 'object' || Array.isArray(f)) || dates.has(String(release.date))) throw new SharedDatasetCatalogResolutionError('Invalid or duplicate release entry');
    dates.add(String(release.date));
  }
  if (!dates.has(String(index.latest_release?.date || ''))) throw new SharedDatasetCatalogResolutionError('Invalid release index latest pointer');
  const date = version === 'latest' ? index.latest_release?.date : version;
  const release = index.releases.find(r => r.date === date);
  if (!release) throw new SharedDatasetCatalogResolutionError(`Requested release ${date || version} was not found`);
  return release;
};

export const selectReleaseFile = (files: SharedDatasetReleaseFile[], format: string, preferredPath = '') => {
  const candidates = files.filter(f => f && typeof f === 'object' && f.format === format);
  const preferred = basename(preferredPath);
  const matches = preferred ? candidates.filter(f => basename(String(f.path || '')) === preferred) : [];
  const selected = matches.length ? matches : candidates;
  if (selected.length > 1) throw new SharedDatasetCatalogResolutionError(`Ambiguous release ${format} files`);
  return selected[0] || null;
};

export const validateReleaseArtifact = (file: SharedDatasetReleaseFile, slug: string, date: string, preferredPath = '') => {
  const path = String(file.path || '');
  const match = path.match(/^gs:\/\/([^/]+)\/(.+)$/);
  if (!match || match[2].split('/').some(s => !s || s === '.' || s === '..') || !path.endsWith(`/${slug}/releases/${date}/${match[2].split('/').at(-1)}`)) throw new SharedDatasetCatalogResolutionError('Artifact is outside the selected asset release');
  if (preferredPath) {
    const root = preferredPath.split(/\/(?:latest|releases)\//)[0];
    if (!path.startsWith(`${root}/releases/${date}/`)) throw new SharedDatasetCatalogResolutionError('Artifact is outside the catalog asset root');
  }
  if (Object.hasOwn(file, 'generation')) normalizeArtifactGeneration(file.generation);
  if (Object.hasOwn(file, 'sha256') && (typeof file.sha256 !== 'string' || !/^[a-f0-9]{64}$/i.test(file.sha256))) throw new SharedDatasetCatalogResolutionError('Invalid artifact checksum');
  if (Object.hasOwn(file, 'size') && (typeof file.size !== 'number' || !Number.isSafeInteger(file.size) || file.size < 0)) throw new SharedDatasetCatalogResolutionError('Invalid artifact size');
};

const isRelease = (value: unknown): value is SharedDatasetRelease =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value));

const metadataFileForLocale = (
  files: SharedDatasetReleaseFile[] | undefined,
  locale: string
) => {
  const safeFiles = Array.isArray(files) ? files : [];
  for (const candidateLocale of locale ? [locale, ''] : ['']) {
    const matches = safeFiles.filter(file => isMetadataFile(file) && metadataLocaleForFile(file) === candidateLocale);
    if (matches.length > 1) throw new SharedDatasetCatalogResolutionError('Ambiguous metadata sidecars');
    if (matches.length) return matches[0];
  }
  return null;
};

const isMetadataFile = (file: SharedDatasetReleaseFile) => {
  const path = String(file?.path || '').trim();
  if (!path.startsWith('gs://') || !path.endsWith('.metadata.ndjson.gz') && !LOCALIZED_METADATA_FILE_RE.test(path)) {
    return false;
  }
  const format = String(file.format || '').trim().toLowerCase();
  const role = String(file.role || '').trim().toLowerCase();
  return format === 'metadata' || role === 'metadata';
};

const metadataLocaleForFile = (file: SharedDatasetReleaseFile) => {
  const declaredLocale = normalizeSharedDatasetMetadataLocale(file.locale || '');
  if (declaredLocale) return declaredLocale;
  const match = basename(String(file.path || '')).match(LOCALIZED_METADATA_FILE_RE);
  return match?.[1] || '';
};

const basename = (path: string) => path.split('/').filter(Boolean).pop() || '';
