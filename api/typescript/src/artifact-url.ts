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
    artifactBaseUrl = DEFAULT_SHARED_DATASETS_ARTIFACTS_URL_BASE
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
  return `${artifactBaseUrl.replace(/\/+$/, '')}/${encodedObjectPath}`;
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
  const release = releaseForVersion(releaseIndex, requestedVersion);
  if (!release) return null;

  const file = metadataFileForLocale(release.files, requestedLocale);
  if (!file) return null;

  const gsUri = String(file.path || '').trim();
  const resolvedLocale = metadataLocaleForFile(file);
  return {
    requestedVersion,
    resolvedVersion: String(release.date || requestedVersion),
    requestedLocale: requestedLocale || null,
    resolvedLocale: resolvedLocale || null,
    metadataLocaleFallback: Boolean(requestedLocale && requestedLocale !== resolvedLocale),
    file,
    gsUri,
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
    url: sharedDatasetArtifactUrlFromGsUri(sidecar.gsUri, options)
  } satisfies SharedDatasetPublicMetadataSidecarUrlResolution;
};

const releaseForVersion = (
  releaseIndex: SharedDatasetReleaseIndex,
  requestedVersion: string
) => {
  const releases = Array.isArray(releaseIndex.releases)
    ? releaseIndex.releases.filter(isRelease)
    : [];
  if (requestedVersion === 'latest') {
    const latest = isRelease(releaseIndex.latest_release)
      ? releaseIndex.latest_release
      : null;
    const latestDate = String(latest?.date || '');
    return (
      (latestDate ? releases.find(release => release.date === latestDate) : null) ||
      latest ||
      null
    );
  }
  return releases.find(release => release.date === requestedVersion) || null;
};

const isRelease = (value: unknown): value is SharedDatasetRelease =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value));

const metadataFileForLocale = (
  files: SharedDatasetReleaseFile[] | undefined,
  locale: string
) => {
  const safeFiles = Array.isArray(files) ? files : [];
  if (locale) {
    const localized = safeFiles.find(
      file => isMetadataFile(file) && metadataLocaleForFile(file) === locale
    );
    if (localized) return localized;
  }
  return (
    safeFiles.find(file => isMetadataFile(file) && metadataLocaleForFile(file) === '') ||
    null
  );
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
