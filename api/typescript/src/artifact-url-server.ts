import crypto from 'node:crypto';

import {
  DEFAULT_SHARED_DATASETS_ARTIFACTS_URL_BASE,
  DEFAULT_SHARED_DATASETS_BUCKET,
  SharedDatasetArtifactUrlOptions,
  sharedDatasetArtifactUrlFromGsUri
} from './artifact-url.js';

export type SharedDatasetArtifactSignedUrlConfig =
  SharedDatasetArtifactUrlOptions & {
    keyName?: string;
    ttlSeconds?: number;
    now?: () => number;
  };

export const DEFAULT_SHARED_DATASETS_ARTIFACT_SIGNED_URL_CONFIG = {
  artifactBaseUrl: DEFAULT_SHARED_DATASETS_ARTIFACTS_URL_BASE,
  bucketName: DEFAULT_SHARED_DATASETS_BUCKET,
  keyName: 'shared-datasets-pmtiles-v1',
  ttlSeconds: 15 * 60,
  now: Date.now
};

const getSignedArtifactConfig = (
  config: SharedDatasetArtifactSignedUrlConfig = {}
) => {
  const ttlSeconds = Number(config.ttlSeconds);
  return {
    ...DEFAULT_SHARED_DATASETS_ARTIFACT_SIGNED_URL_CONFIG,
    ...config,
    ttlSeconds:
      Number.isFinite(ttlSeconds) && ttlSeconds > 0
        ? Math.floor(ttlSeconds)
        : DEFAULT_SHARED_DATASETS_ARTIFACT_SIGNED_URL_CONFIG.ttlSeconds,
    now: config.now ?? DEFAULT_SHARED_DATASETS_ARTIFACT_SIGNED_URL_CONFIG.now
  };
};

const toUrlSafeBase64 = (value: Buffer) =>
  Buffer.from(value).toString('base64').replace(/\+/g, '-').replace(/\//g, '_');

export const getSignedSharedDatasetArtifactUrl = (
  gsUri: string,
  signingKey: Buffer,
  config?: SharedDatasetArtifactSignedUrlConfig
) => {
  const resolvedConfig = getSignedArtifactConfig(config);
  const url = sharedDatasetArtifactUrlFromGsUri(gsUri, resolvedConfig);
  const expires =
    Math.floor(resolvedConfig.now() / 1000) + resolvedConfig.ttlSeconds;
  const keyName = encodeURIComponent(resolvedConfig.keyName);
  const unsignedUrl = `${url}?Expires=${expires}&KeyName=${keyName}`;
  const signature = toUrlSafeBase64(
    crypto.createHmac('sha1', signingKey).update(unsignedUrl).digest()
  );
  return `${unsignedUrl}&Signature=${encodeURIComponent(signature)}`;
};
