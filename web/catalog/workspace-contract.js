// Portable contract v1. See docs/standards/workspace-snapshot-v1.md.
// This module is also compiled into the TypeScript SDK; it has no browser globals.
export const SNAPSHOT_MAX_BYTES = 1024 * 1024;
const formats = {fgb: /\.fgb$/, csv: /\.csv$/, geojson: /\.geojson$/, ndgeojson: /\.(ndgeojson|geojsonl)$/, cog: /\.(tif|tiff)$/, pmtiles: /\.pmtiles$/, metadata: /\.metadata(?:\.[a-z]{2,3}(?:_[a-z0-9]{2,8})*)?\.ndjson\.gz$/, schema: /\.schema\.json$/};
const provenanceKeys = ['title', 'citation', 'source', 'source_url', 'license', 'notes', 'status', 'lifecycle_reason', 'lifecycle_date', 'successor_asset_slug', 'consumer_guidance', 'source_version', 'release_index_uri', 'release_index_generation', 'release_index_updated_at', 'identity_contract'];
const identityKeys = ['strategy', 'source_fields', 'generated_id_type', 'assignment_key', 'feature_id_column', 'geometry_hash_column', 'properties_hash_column'];
function fail(message) { throw new Error(`Invalid workspace: ${message}`); }
function object(value, keys, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) fail(`${name} has unknown or missing fields`);
}
function text(value, name, nullable = false, max = 16384) {
  if (nullable && value === null) return;
  if (typeof value !== 'string' || value.length > max || /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(value)) fail(`${name} must be bounded text`);
  for (const char of value) if (char.codePointAt(0) >= 0xd800 && char.codePointAt(0) <= 0xdfff) fail(`${name} contains an unpaired surrogate`);
}
function locale(value) { if (value !== null && (typeof value !== 'string' || !/^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$(?![\s\S])/.test(value) || value.length > 64)) fail('invalid locale'); }
function date(value) {
  if (value !== null && (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$(?![\s\S])/.test(value) || Number.isNaN(Date.parse(value)) || new Date(value).toISOString().slice(0, 10) !== value || value < '0001-01-01')) fail('invalid release date');
}
function generation(value, nullable = false) {
  if (nullable && value === null) return;
  if (typeof value !== 'string' || !/^[1-9][0-9]{0,19}$(?![\s\S])/.test(value) || BigInt(value) > 18446744073709551615n) fail('generation must be a positive decimal string within uint64');
}
function uri(value, bucket) {
  text(value, 'gs_uri', false, 2048);
  const prefix = `gs://${bucket}/`;
  if (!value.startsWith(prefix) || /[?#%\\\s*\[\]]/.test(value) || value.slice(prefix.length).split('/').some(part => !part || part === '.' || part === '..')) fail('URI is outside the workspace bucket or has unsafe path components');
  return value.slice(prefix.length);
}
function boundedNumber(value, minimum, maximum, name) { if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum) fail(`invalid ${name}`); }
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }

// JSON.parse cannot detect duplicate keys. Parse once at the portable text boundary.
export function parseSnapshotJson(input, options = {}) {
  if (typeof input !== 'string' || new TextEncoder().encode(input).length > SNAPSHOT_MAX_BYTES) fail('JSON exceeds 1 MiB');
  let position = 0;
  function whitespace() { while (/[ \r\n\t]/.test(input[position] || 'x')) position++; }
  function string() {
    const start = position++;
    while (position < input.length) {
      const char = input[position++];
      if (char === '\\') position++;
      else if (char === '"') return JSON.parse(input.slice(start, position));
    }
    fail('unterminated JSON string');
  }
  function value(depth) {
    if (depth > 12) fail('JSON nesting exceeds 12');
    whitespace();
    const char = input[position];
    if (char === '"') return string();
    if (char === '{' || char === '[') {
      position++;
      const isObject = char === '{', result = isObject ? Object.create(null) : [], keys = new Set(), end = isObject ? '}' : ']';
      whitespace();
      if (input[position] === end) { position++; return result; }
      while (true) {
        whitespace();
        if (isObject) {
          if (input[position] !== '"') fail('invalid JSON key');
          const key = string();
          if (keys.has(key)) fail(`duplicate JSON key ${key}`);
          keys.add(key); whitespace();
          if (input[position++] !== ':') fail('invalid JSON object');
          result[key] = value(depth + 1);
        } else result.push(value(depth + 1));
        whitespace();
        if (input[position] === end) { position++; return result; }
        if (input[position++] !== ',') fail('invalid JSON separator');
      }
    }
    const match = /^(?:true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(input.slice(position));
    if (!match) fail('invalid JSON value');
    position += match[0].length;
    return JSON.parse(match[0]);
  }
  const result = value(0); whitespace();
  if (position !== input.length) fail('trailing JSON content');
  return validateSnapshot(result, options);
}

export function validateSnapshot(input, {bucket = 'skytruth-shared-datasets-1'} = {}) {
  function checkJson(value, depth = 0) {
    if (depth > 12 || (typeof value === 'number' && !Number.isFinite(value))) fail('invalid JSON depth or number');
    if (value && typeof value === 'object') Object.values(value).forEach(v => checkJson(v, depth + 1));
  }
  checkJson(input);
  if (new TextEncoder().encode(JSON.stringify(input)).length > SNAPSHOT_MAX_BYTES) fail('JSON exceeds 1 MiB');
  object(input, ['kind', 'schema_version', 'bucket', 'datasets', 'presentation'], 'snapshot');
  if (input.kind !== 'skytruth-workspace' || input.schema_version !== 1) fail('unrecognized kind or schema_version');
  if (input.bucket !== bucket || !/^[a-z0-9][a-z0-9._-]{2,221}$(?![\s\S])/.test(bucket)) fail('bucket is not allowed');
  if (!Array.isArray(input.datasets) || !input.datasets.length || input.datasets.length > 32) fail('expected 1–32 datasets');
  const slugs = new Set(), uris = new Set();
  for (const dataset of input.datasets) {
    object(dataset, ['asset_slug', 'release', 'canonical_format', 'access_tier', 'artifacts', 'provenance'], 'dataset');
    const slug = dataset.asset_slug;
    if (typeof slug !== 'string' || !/^[a-z0-9]+(?:-[a-z0-9]+)*$(?![\s\S])/.test(slug) || slug.length > 128 || slugs.has(slug)) fail('invalid or duplicate asset_slug');
    slugs.add(slug); date(dataset.release);
    if (!['public', 'private', 'internal'].includes(dataset.access_tier) || !Object.hasOwn(formats, dataset.canonical_format) || ['metadata', 'schema'].includes(dataset.canonical_format)) fail('unsupported tier or canonical format');
    if (!Array.isArray(dataset.artifacts) || !dataset.artifacts.length || dataset.artifacts.length > 8) fail('expected 1–8 artifacts');
    const roles = new Set(); let canonical = 0;
    for (const artifact of dataset.artifacts) {
      object(artifact, ['format', 'role', 'gs_uri', 'generation', 'size', 'sha256', 'requested_locale', 'resolved_locale'], 'artifact');
      if (!Object.hasOwn(formats, artifact.format) || !['canonical', 'tiles', 'metadata', 'schema'].includes(artifact.role)) fail('unsupported artifact format or role');
      const path = uri(artifact.gs_uri, bucket), segments = path.split('/');
      const location = dataset.release === null ? ['latest'] : ['releases', dataset.release];
      if (segments.slice(-(location.length + 2), -1).join('/') !== [slug, ...location].join('/') || segments.length < location.length + 4 || !formats[artifact.format].test(path)) fail('artifact is outside the asset release or has the wrong extension');
      generation(artifact.generation);
      if (artifact.size !== null && (!Number.isSafeInteger(artifact.size) || artifact.size < 0)) fail('size must be a nonnegative safe integer or null');
      if (artifact.sha256 !== null && (typeof artifact.sha256 !== 'string' || !/^[a-f0-9]{64}$(?![\s\S])/.test(artifact.sha256))) fail('invalid SHA-256');
      locale(artifact.requested_locale); locale(artifact.resolved_locale);
      const roleKey = `${artifact.role}:${artifact.resolved_locale}`;
      if (uris.has(artifact.gs_uri) || roles.has(roleKey)) fail('duplicate artifact URI or role/locale');
      uris.add(artifact.gs_uri); roles.add(roleKey);
      if (artifact.role === 'canonical') { canonical++; if (artifact.format !== dataset.canonical_format) fail('canonical format mismatch'); }
      if ((artifact.role === 'tiles' && artifact.format !== 'pmtiles') || (artifact.role === 'metadata' && artifact.format !== 'metadata') || (artifact.role === 'schema' && artifact.format !== 'schema')) fail('role/format mismatch');
      if (artifact.role !== 'metadata' && (artifact.requested_locale !== null || artifact.resolved_locale !== null)) fail('locale only applies to metadata');
      if (artifact.role === 'metadata') {
        const suffix = artifact.resolved_locale ? `.metadata.${artifact.resolved_locale}.ndjson.gz` : '.metadata.ndjson.gz';
        if (!path.endsWith(suffix)) fail('metadata locale/path mismatch');
      }
    }
    if (canonical !== 1) fail('exactly one canonical artifact required');
    const roots = new Set(dataset.artifacts.map(a => a.gs_uri.split(/\/(?:latest|releases)\//)[0]));
    if (roots.size !== 1) fail('artifacts have different asset roots');
    const provenance = dataset.provenance;
    object(provenance, provenanceKeys, 'provenance');
    for (const key of provenanceKeys.filter(k => !['identity_contract', 'release_index_generation'].includes(k))) text(provenance[key], key, true);
    generation(provenance.release_index_generation, true);
    if (provenance.release_index_uri !== null && uri(provenance.release_index_uri, bucket) !== `_catalog/releases/${slug}.json`) fail('invalid release-index provenance');
    if (provenance.source_url !== null) {
      let source;
      try { source = new URL(provenance.source_url); } catch { fail('source_url must be a non-secret HTTP URL'); }
      if (!/^https?:\/\//i.test(provenance.source_url) || !['http:', 'https:'].includes(source.protocol) || source.username || source.password || [...source.searchParams.keys()].some(key => /^(?:token|access_token|id_token|signature|sig|credential|key|api_key|apikey|x-goog-.*|x-amz-.*)$(?![\s\S])/i.test(key))) fail('source_url must be a non-secret HTTP URL');
    }
    if (provenance.identity_contract !== null) {
      const identity = provenance.identity_contract;
      if (!identity || typeof identity !== 'object' || Array.isArray(identity) || Object.keys(identity).some(key => !identityKeys.includes(key))) fail('invalid identity contract');
      for (const value of Object.values(identity)) { if (Array.isArray(value)) { if (value.length > 32) fail('identity fields exceed limit'); value.forEach(v => text(v, 'identity field', false, 128)); } else text(value, 'identity field', false, 128); }
    }
  }
  if (input.presentation !== null) {
    const p = input.presentation;
    object(p, ['basemap', 'viewport', 'locale', 'layers'], 'presentation');
    if (!['map', 'satellite'].includes(p.basemap)) fail('unsupported basemap');
    locale(p.locale);
    if (!Array.isArray(p.layers) || p.layers.length !== slugs.size) fail('presentation must name all datasets');
    const layers = new Set();
    for (const layer of p.layers) {
      object(layer, ['asset_slug', 'visible', 'source_layer', 'color_field'], 'layer');
      if (!slugs.has(layer.asset_slug) || layers.has(layer.asset_slug) || layer.visible !== true) fail('invalid, duplicate, or unsupported layer visibility');
      layers.add(layer.asset_slug); text(layer.source_layer, 'source_layer', true, 128); text(layer.color_field, 'color_field', true, 128);
      if (p.layers.length > 1 && (layer.source_layer !== null || layer.color_field !== null)) fail('multi-dataset styling is unsupported');
    }
    if (p.viewport !== null) {
      object(p.viewport, ['center', 'zoom', 'bearing', 'pitch'], 'viewport');
      if (!Array.isArray(p.viewport.center) || p.viewport.center.length !== 2) fail('invalid center');
      boundedNumber(p.viewport.center[0], -180, 180, 'longitude'); boundedNumber(p.viewport.center[1], -85.051129, 85.051129, 'latitude');
      boundedNumber(p.viewport.zoom, 0, 22, 'zoom'); boundedNumber(p.viewport.bearing, -180, 180, 'bearing'); boundedNumber(p.viewport.pitch, 0, 85, 'pitch');
    }
  }
  return freeze(JSON.parse(JSON.stringify(input)));
}
