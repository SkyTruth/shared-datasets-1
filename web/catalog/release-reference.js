// A release date labels a snapshot; only object generations identify its bytes.
export function artifactGeneration(value) {
  const text = typeof value === "number" && Number.isSafeInteger(value) ? String(value) : value;
  if (typeof text !== "string" || !/^[1-9][0-9]{0,19}$/.test(text) || BigInt(text) > 18446744073709551615n) {
    throw new Error("Exact artifact generation unavailable. Reload the catalog after its release index is repaired.");
  }
  return text;
}

export function captureArtifact(file, role, requestedLocale = null) {
  if (!file) throw new Error(`Exact ${role} identity is missing from the release index.`);
  const resolvedLocale = role === 'metadata' ? (/\.metadata\.([a-z]{2,3}(?:_[a-z0-9]{2,8})*)\.ndjson\.gz$/.exec(file.path)?.[1] || null) : null;
  return {format: file.format, role, gs_uri: file.path, generation: artifactGeneration(file.generation),
    size: file.size ?? null, sha256: file.sha256?.toLowerCase() ?? null,
    requested_locale: requestedLocale, resolved_locale: resolvedLocale};
}
export function releaseFile(files, format, preferredPath = "") {
  const candidates = files.filter((file) => file.format === format);
  const name = preferredPath.split("/").pop();
  const preferred = name ? candidates.filter((file) => file.path.split("/").pop() === name) : [];
  const matches = preferred.length ? preferred : candidates;
  if (matches.length > 1) throw new Error(`Ambiguous release ${format} files.`);
  return matches[0] || null;
}

export function metadataFile(files, locale = "") {
  for (const language of locale ? [locale, ""] : [""]) {
    const suffix = language ? `.metadata.${language}.ndjson.gz` : ".metadata.ndjson.gz";
    const matches = files.filter((file) => (file.format === "metadata" || file.role === "metadata") && file.path.endsWith(suffix) && (!file.locale || file.locale === language));
    if (matches.length > 1) throw new Error("Ambiguous release metadata files.");
    if (matches.length) return matches[0];
  }
  return null;
}

export function artifactKey(file) {
  return file ? `${file.path}\n${artifactGeneration(file.generation)}` : "";
}

export function artifactUrl(file, {bucket, baseUrl = "https://tiles.skytruth.org/artifacts"} = {}) {
  if (!file) return "";
  const match = file.path.match(/^gs:\/\/([^/]+)\/(.+)$/);
  if (!match || (bucket && match[1] !== bucket)) throw new Error("Artifact is outside the catalog bucket.");
  return `${baseUrl}/${match[2].split("/").map(encodeURIComponent).join("/")}?generation=${artifactGeneration(file.generation)}`;
}

export function snapshotKey(reference) {
  return reference.release_snapshot_key || `${reference.slug}\nlegacy-latest`;
}

export function assertArtifactResponse(payload, reference, file) {
  if (payload.gs_uri !== file.path || payload.resolved_release !== reference.date || artifactGeneration(payload.generation) !== artifactGeneration(file.generation)) {
    throw new Error("Selected artifact changed. Reload the catalog and reselect the release.");
  }
}

export function lookupMatchesReference(payload, reference) {
  const file = metadataFile(reference.files || []);
  return Boolean(file && payload.asset_slug === reference.slug && payload.resolved_release === reference.date &&
    payload.sidecar_uri === file.path && payload.sidecar_generation != null &&
    artifactGeneration(payload.sidecar_generation) === artifactGeneration(file.generation));
}

function validDate(date) {
  return typeof date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(date) && !Number.isNaN(Date.parse(date)) && new Date(date).toISOString().slice(0, 10) === date;
}

export function selectReleaseReference(asset, version = "latest", {bucket, baseUrl} = {}) {
  if (asset.release_error) throw new Error(asset.release_error);
  const versions = asset.versions || [];
  if (!versions.length && !asset.latest_release) {
    if (version !== "latest") throw new Error(`Requested release ${version} was not found.`);
    // An alias may preview tiles, but is not evidence for a cross-artifact join.
    return Object.freeze({...asset, date: null, files: [], feature_metadata: null, release_snapshot_key: null});
  }
  if (version !== "latest" && !validDate(version)) throw new Error("Version must be latest or YYYY-MM-DD.");
  const dates = new Set();
  for (const entry of versions) {
    if (!entry || !validDate(entry.date) || dates.has(entry.date) || !Array.isArray(entry.files)) throw new Error("Invalid or duplicate release entry.");
    dates.add(entry.date);
  }
  const latest = asset.latest_release?.date;
  if (!dates.has(latest)) throw new Error("Release index latest pointer is invalid.");
  const date = version === "latest" ? latest : version;
  const entry = versions.find((item) => item.date === date);
  if (!entry) throw new Error(`Requested release ${date} was not found.`);
  const root = String(asset.canonical_path || asset.pmtiles_path || "").split(/\/(?:latest|releases)\//)[0];
  const prefix = `${root}/releases/${date}/`;
  const files = Object.freeze(entry.files.map((raw) => {
    if (!raw || typeof raw !== "object" || typeof raw.path !== "string" || !raw.path.startsWith(prefix) || raw.path.slice(prefix.length).includes("/") || !raw.path.slice(prefix.length) || [".", ".."].includes(raw.path.slice(prefix.length))) throw new Error("Artifact is outside the catalog asset release.");
    const file = {...raw};
    if (Object.hasOwn(file, "generation")) file.generation = artifactGeneration(file.generation);
    if (Object.hasOwn(file, "size") && (!Number.isSafeInteger(file.size) || file.size < 0)) throw new Error("Invalid release artifact size.");
    if (Object.hasOwn(file, "sha256") && (typeof file.sha256 !== "string" || !/^[a-f0-9]{64}$/i.test(file.sha256))) throw new Error("Invalid release artifact checksum.");
    return Object.freeze(file);
  }));
  const canonical = releaseFile(files, asset.canonical_format, asset.canonical_path);
  const tile = releaseFile(files, "pmtiles", asset.pmtiles_path || "");
  if (asset.has_pmtiles && !tile) throw new Error("Selected release does not include PMTiles.");
  const urlOptions = {bucket, baseUrl};
  const tileUrl = tile ? artifactUrl(tile, urlOptions) : null;
  const canonicalUrl = canonical?.generation ? artifactUrl(canonical, urlOptions) : "";
  const key = JSON.stringify([asset.slug, date, files.map((file) => [file.path, file.generation || null])]);
  return Object.freeze({...asset, ...entry, date, files, canonical_file: canonical, pmtiles_file: tile,
    canonical_path: canonical?.path || "", public_url: canonicalUrl,
    pmtiles_path: tile?.path || null, pmtiles_url: tileUrl, release_snapshot_key: key});
}

// Execution observations complement release references; use an existing served module
// so catalog publication can precede a catalog-viewer image rollout.
const states = new Set(["pending", "running", "succeeded", "failed", "cancelled", "unknown"]);

export function executionStatusText(document, now = Date.now()) {
  if (document?.schema_version !== 1 || !String(document.job_name || "").endsWith("/jobs/wdpa-monthly")) {
    throw new Error("Unsupported WDPA execution observation");
  }
  const observed = Date.parse(document.observed_at);
  if (!Number.isFinite(observed)) throw new Error("Invalid execution observation timestamp");
  const describe = (entry) => {
    if (!entry) return "No execution observed";
    if (!states.has(entry.state)) throw new Error("Invalid execution state");
    return `${entry.state} · ${entry.id} · ${entry.completed_at || entry.started_at || entry.created_at || "time unknown"}${entry.reason_code && entry.reason_code !== "UNSPECIFIED" ? ` · ${entry.reason_code}` : ""}`;
  };
  const latest = document.latest_execution;
  const completed = document.latest_completed_execution;
  const parts = [describe(latest)];
  if (completed && completed.id !== latest?.id) parts.push(`Last completed: ${describe(completed)}`);
  parts.push(now - observed > 15 * 60_000 ? `Stale observation · ${document.observed_at}` : `Observed ${document.observed_at}`);
  return parts.join(". ");
}
