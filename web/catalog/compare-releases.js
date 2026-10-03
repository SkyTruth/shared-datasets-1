import {selectReleaseReference, metadataFile, releaseFile, artifactGeneration, captureArtifact} from "./release-reference.js";

export const CHANGE_LABELS = {added: "Added", removed: "Removed", geometry_only: "Geometry only", properties_only: "Properties only", both: "Geometry and properties", unchanged: "Unchanged"};
const GEOMETRY_LABELS = {novel: "New geometry", removed: "Removed geometry", metadata_changed: "Metadata changed", unchanged: "Unchanged geometry"};

async function loadMapIndex(session, onProgress) {
  const hashes = new Map(), loaded = [0, 0];
  const abort = new AbortController(), cancel = () => abort.abort();
  session.abort.signal.addEventListener("abort", cancel, {once: true});
  try { return await Promise.all(["baseline", "target"].map(async (side, index) => {
    const response = await fetch(`/api/comparisons/${session.job}/map-index?side=${side}`, {credentials: "include", cache: "no-store", signal: abort.signal});
    if (!response.ok || !response.headers.get("content-type")?.startsWith("application/x-ndjson")) throw new Error("Map classification stream is unavailable. The viewer may still be deploying; retry the comparison shortly.");
    const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", {fatal: true}), rows = new Map();
    let pending = "", header = null, finished = false, previous = "";
    const total = session.summary.feature_counts.baseline + session.summary.feature_counts.target;
    function record(line) {
      if (line.length > 65536) throw new Error("Map classification record exceeds its contract.");
      const value = JSON.parse(line);
      if (!header) {
        if (value.schema_version !== 1 || value.side !== side || value.rows !== session.summary.feature_counts[side]) throw new Error("Map classification stream does not match the selected release.");
        assertComparisonInputs(value.inputs, session.refs); header = value; return;
      }
      if (finished) throw new Error("Map classification stream has trailing records.");
      if (Array.isArray(value)) {
        const [id, change, hash] = value;
        if (value.length !== 3 || typeof id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/.test(id) || id <= previous || !Object.hasOwn(GEOMETRY_LABELS, change) || typeof hash !== "string" || !/^sha256:[0-9a-f]{64}$/.test(hash) || rows.size >= header.rows) throw new Error("Invalid map classification stream record.");
        let geometry = hashes.get(hash);
        if (geometry && geometry.change !== change) throw new Error("A geometry has conflicting map classifications.");
        if (!geometry) { geometry = {change, geometry_hash: hash}; hashes.set(hash, geometry); }
        rows.set(id, geometry); previous = id; loaded[index]++;
      } else {
        if (value.complete !== true || value.rows !== header.rows || rows.size !== header.rows) throw new Error("Map classification stream is incomplete.");
        finished = true;
      }
    }
    try {
      while (true) {
        const {value, done} = await reader.read();
        pending += decoder.decode(value, {stream: !done});
        let end;
        while ((end = pending.indexOf("\n")) >= 0) { record(pending.slice(0, end)); pending = pending.slice(end + 1); }
        if (pending.length > 65536) throw new Error("Map classification record exceeds its contract.");
        if (!abort.signal.aborted) onProgress({phase: "map-index", completed: loaded[0] + loaded[1], total});
        if (done) break;
      }
      if (!finished || pending) throw new Error("Map classification stream ended before completion.");
      return rows;
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  })); } finally { abort.abort(); session.abort.signal.removeEventListener("abort", cancel); }
}

// Preserve absent versus null, and ignore object key order in source values.
export function changedPropertyFields(before, after, exclusions) {
  const canonical = value => JSON.stringify(value, (_, item) => item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);
  const excluded = new Set(exclusions);
  return new Set([...new Set([...Object.keys(before), ...Object.keys(after)])].filter(field => !excluded.has(field)
    && (Object.hasOwn(before, field) !== Object.hasOwn(after, field) || canonical(before[field]) !== canonical(after[field]))));
}

export function comparisonFiles(reference) {
  const files = {metadata: metadataFile(reference.files), schema: releaseFile(reference.files, "schema"), manifest: releaseFile(reference.files, "manifest")};
  if (reference.pmtiles_file) files.pmtiles = reference.pmtiles_file;
  if (reference.canonical_file?.format === "fgb") files.fgb = reference.canonical_file;
  return Object.fromEntries(Object.entries(files).map(([role, file]) => {
    const artifact = captureArtifact(file, role === "pmtiles" ? "tiles" : role);
    return [role, {path: artifact.gs_uri, generation: artifact.generation}];
  }));
}

export function assertComparisonInputs(inputs, refs) {
  for (const side of ["baseline", "target"]) {
    const actual = inputs?.[side];
    if (actual?.asset_slug !== refs[side].slug || actual.release !== refs[side].date) throw new Error("Comparison response does not match the selected releases.");
    const expected = comparisonFiles(refs[side]);
    if (Object.keys(actual.files || {}).length !== Object.keys(expected).length) throw new Error("Comparison input artifacts differ.");
    for (const [role, file] of Object.entries(expected)) {
      if (actual.files[role]?.path !== file.path || artifactGeneration(actual.files[role]?.generation) !== file.generation) throw new Error("Comparison artifact changed; reload and reselect the releases.");
    }
  }
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function table(headers, rows, caption) {
  const node = element("table"), head = element("thead"), tr = element("tr"), body = element("tbody");
  if (caption) node.append(element("caption", caption));
  headers.forEach(text => tr.append(element("th", text))); head.append(tr);
  rows.forEach(values => {
    const row = element("tr");
    values.forEach((value, index) => {
      const cell = element(index === 0 ? "th" : "td");
      if (value instanceof Node) cell.append(value); else cell.textContent = value;
      row.append(cell);
    });
    body.append(row);
  });
  node.append(head, body); return node;
}
function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    const done = () => { signal.removeEventListener("abort", abort); resolve(); };
    const timer = setTimeout(done, ms);
    const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
    signal.addEventListener("abort", abort, {once: true});
  });
}

export function createComparisonController({loadMapModule = () => import("./map-preview.js"), onModeChange = () => {}, onFeatureSelect, getBasemap = () => "map"} = {}) {
  const ids = ["open", "panel", "before", "after", "release-controls", "status", "progress", "progress-label", "progress-count", "progress-bar", "summary", "schema", "search", "filter", "rows", "previous", "next", "page", "inspector", "details", "map-note", "table", "legend"];
  const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(`compare-${id}`)]));
  const versionControl = document.getElementById("version-control"), mapContainer = document.getElementById("map-preview"), mapStatus = document.getElementById("map-status");
  let asset = null, version = "latest", options = {}, active = null, opened = false, viewport = null, pageSerial = 0, inspectSerial = 0, rerunTimer = null;
  let cancellation = Promise.resolve();
  const current = session => active === session && !session.abort.signal.aborted;
  const status = text => { ui.status.textContent = text; ui.status.hidden = !opened || !text; };
  function progress(value = null) {
    ui.progress.hidden = !opened || !value;
    if (!value) return;
    const labels = {queued: "Waiting for a comparison worker", downloading: "Preparing releases", baseline: "Validating Before", target: "Validating After", "baseline geometry": "Checking Before geometry", "target geometry": "Checking After geometry", classifying: "Comparing geometry and metadata", publishing: "Preparing comparison", "map-index": "Loading map classifications", coloring: "Coloring the map"};
    ui["progress-label"].textContent = labels[value.phase];
    // During a rolling deployment the previous API supplies phase/rows only.
    const total = value.total ?? null;
    ui["progress-count"].textContent = total === null ? "" : `${value.completed.toLocaleString()} / ${total.toLocaleString()} rows`;
    ui["progress-bar"].max = total || 1;
    if (total === null) ui["progress-bar"].removeAttribute("value");
    else ui["progress-bar"].value = value.completed;
  }
  function setDetails(expanded) {
    ui.panel.hidden = !opened || !expanded;
    ui.details.setAttribute("aria-expanded", String(expanded));
    ui.details.textContent = expanded ? "▾ Details" : "▸ Details";
  }
  function mapNote(session) {
    ui["map-note"].textContent = session.mapError ? `Map inspection unavailable: ${session.mapError}` : !session.mapReady ? "Preparing comparison map… Filters become available when colors are ready." : "Colors cover all loaded geometry, independently of table pages. Unchanged geometry is faint. Display detail varies with zoom.";
  }
  async function cancelJob(id) {
    if (!id) return;
    try { await fetch(`/api/comparisons/${id}/cancel`, {method: "POST", credentials: "include"}); }
    catch { /* A disconnected browser cannot acknowledge cancellation; server TTL/budget still applies. */ }
  }
  function stop() {
    window.clearTimeout(rerunTimer); rerunTimer = null;
    pageSerial++; inspectSerial++;
    onFeatureSelect([]);
    if (active) {
      viewport = active.map?.viewport() || viewport;
      window.clearInterval(active.keepalive);
      active.abort.abort(); active.mapAbort?.abort(); active.map?.dispose();
      const stopped = active;
      const started = stopped.startPromise || Promise.resolve({job_id: stopped.job});
      cancellation = Promise.all([cancellation, started.then(result => cancelJob(result.job_id), () => {})]).then(() => {});
      active = null;
    }
  }
  function clearResults() {
    progress(); status("");
    for (const key of ["summary", "schema", "rows", "inspector"]) ui[key].replaceChildren();
    ui.table.hidden = true; ui.previous.disabled = ui.next.disabled = true;
    for (const button of ui.legend.querySelectorAll("button")) {
      button.disabled = true; button.setAttribute("aria-pressed", "false");
    }
  }
  function setMode(enabled, restore = false) {
    opened = enabled;
    ui.details.hidden = ui["release-controls"].hidden = ui.legend.hidden = ui["map-note"].hidden = !enabled;
    document.getElementById("version-path-row").classList.toggle("comparing", enabled);
    setDetails(false); status("");
    versionControl.hidden = enabled;
    ui.open.textContent = enabled ? "Close comparison" : "Compare releases";
    ui.open.setAttribute("aria-expanded", String(enabled));
    onModeChange(enabled, {restore});
  }
  function close(restore = true) {
    stop(); clearResults(); viewport = null; setMode(false, restore);
    ui.open.focus();
  }
  function refs() {
    return {baseline: selectReleaseReference(asset, ui.before.value, options), target: selectReleaseReference(asset, ui.after.value, options)};
  }
  async function request(url, session, init = {}) {
    const response = await fetch(url, {credentials: "include", cache: "no-store", signal: session.abort.signal, ...init});
    if (response.status === 404 && !response.headers.get("content-type")?.includes("json")) throw new Error("Feature comparison is available in the authenticated catalog viewer. This static catalog supports visual release inspection and the local CLI.");
    let payload;
    try { payload = await response.json(); } catch { throw new Error("Comparison endpoint is unavailable. Open the authenticated catalog viewer or use the local CLI."); }
    if (!response.ok) throw new Error(typeof payload.error === "string" ? payload.error : `Comparison returned HTTP ${response.status}`);
    if (payload.inputs) assertComparisonInputs(payload.inputs, session.refs);
    return payload;
  }
  function renderSummary(summary) {
    const rows = [["Release", summary.inputs.baseline.release, summary.inputs.target.release], ["Features", summary.feature_counts.baseline.toLocaleString(), summary.feature_counts.target.toLocaleString()]];
    ui.summary.replaceChildren(table(["Selection", "Before", "After"], rows, "Releases"));
    const matching = element("details", undefined, "compare-method");
    matching.append(element("summary", summary.identity.compatible ? "Feature IDs are comparable" : "Feature IDs cannot be matched"), element("p", summary.identity.reason));
    ui.summary.append(matching);
    const counts = summary.counts ? Object.entries(CHANGE_LABELS).map(([key, label]) => [label, summary.counts[key].toLocaleString()]) : Object.entries(GEOMETRY_LABELS).map(([key, label]) => [label, summary.geometry_counts[key].toLocaleString()]);
    ui.summary.append(table(["Change", summary.counts ? "Features" : "Unique geometries"], counts, summary.counts ? "Feature changes" : "Geometry changes"));
    const pins = element("details", undefined, "compare-inputs"); pins.append(element("summary", "Exact inputs"));
    const artifactRows = Object.keys(summary.inputs.baseline.files).map(role => [role, ...["baseline", "target"].map(side => {
      const file = summary.inputs[side].files[role], cell = element("div");
      if (!file) { cell.textContent = "Absent"; return cell; }
      cell.append(element("code", file.path), element("small", `Generation ${file.generation} · ${file.size ?? "unknown"} bytes`));
      if (file.sha256) cell.append(element("small", `SHA-256 ${file.sha256}`));
      return cell;
    })]);
    pins.append(table(["Input", "Before", "After"], artifactRows)); ui.summary.append(pins);
    const schemaRows = Object.entries({added: "Added fields", removed: "Removed fields", datatype_changes: "Datatype changes", field_semantics_changes: "Field semantics"}).map(([key, label]) => {
      const changes = summary.schema_changes[key];
      if (!changes.length) return [label, "0"];
      const detail = element("details"); detail.append(element("summary", String(changes.length)));
      detail.append(table(["Field", "Change"], changes.map(change => [typeof change === "string" ? change : change.field || change.name, typeof change === "string" ? key : JSON.stringify(change)])));
      return [label, detail];
    });
    ui.schema.replaceChildren(table(["Schema change", "Fields"], schemaRows, "Schema"));
  }
  async function loadPage(session, offset = 0) {
    const token = ++pageSerial; inspectSerial++; ui.inspector.replaceChildren(); session.map?.selectFeature(null);
    const params = new URLSearchParams({offset, limit: 50, query: ui.search.value, classification: ui.filter.value, geometry_change: session.geometryFilter});
    try {
      const result = await request(`/api/comparisons/${session.job}?${params}`, session);
      if (!current(session) || token !== pageSerial) return;
      const page = result.page; session.page = page; ui.table.hidden = false; ui.rows.replaceChildren();
      for (const row of page.rows) {
        const tr = element("tr"), cell = element("td"), button = element("button", row.feature_id);
        button.type = "button"; button.addEventListener("click", () => void inspect(session, row.feature_id)); cell.append(button);
        tr.append(cell, element("td", CHANGE_LABELS[row.classification])); ui.rows.append(tr);
      }
      ui.page.textContent = page.total ? `${page.offset + 1}–${Math.min(page.total, page.offset + page.limit)} of ${page.total}` : "No matching features";
      ui.previous.disabled = page.offset === 0; ui.next.disabled = page.offset + page.limit >= page.total;
    } catch (error) { if (current(session) && token === pageSerial) status(error.message); }
  }
  async function inspect(session, id) {
    const token = ++inspectSerial;
    setDetails(true);
    try {
      const result = await request(`/api/comparisons/${session.job}?feature_id=${encodeURIComponent(id)}`, session);
      if (!current(session) || token !== inspectSerial) return;
      const feature = result.feature, a = feature.before?.properties || {}, b = feature.after?.properties || {};
      const values = props => field => Object.hasOwn(props, field) ? JSON.stringify(props[field]) : "Absent";
      const rows = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort().map(field => [field, values(a)(field), values(b)(field)]);
      const properties = table(["Property", "Before", "After"], rows, `${id} · ${CHANGE_LABELS[feature.classification]}`);
      const changed = new Set(feature.property_changes.map(change => change.field));
      for (const row of properties.tBodies[0].rows) if (changed.has(row.cells[0].textContent)) row.classList.add("metadata-changed");
      ui.inspector.replaceChildren(properties);
      const provenance = element("details");
      provenance.append(element("summary", "Publication provenance"), element("pre", JSON.stringify({before: feature.before?.provenance ?? null, after: feature.after?.provenance ?? null}, null, 2)));
      ui.inspector.append(provenance); session.map?.selectFeature(id);
    } catch (error) { if (current(session) && token === inspectSerial) status(error.message); }
  }
  async function showMap(session) {
    session.mapReady = false;
    for (const button of ui.legend.querySelectorAll("button")) button.disabled = true;
    viewport = session.map?.viewport() || viewport;
    session.mapAbort?.abort(); session.map?.dispose();
    session.map = null;
    const mapAbort = new AbortController(); session.mapAbort = mapAbort;
    const abort = () => mapAbort.abort(); session.abort.signal.addEventListener("abort", abort, {once: true});
    const isCurrent = () => current(session) && session.mapAbort === mapAbort && !mapAbort.signal.aborted;
    try {
      const module = await loadMapModule(); if (!isCurrent()) return;
      const map = await module.renderComparisonMap({container: mapContainer, status: mapStatus, baseline: session.refs.baseline, target: session.refs.target,
        signal: mapAbort.signal, basemap: getBasemap(), viewport, category: session.geometryFilter,
        onFeatureSelect: features => { if (isCurrent()) onFeatureSelect(features.map(feature => ({...feature, comparisonExcludedProperties: session.summary?.property_hash_exclusions}))); },
        onProgress: value => { if (isCurrent() && !session.mapReady) progress(value); },
        onError: error => { if (isCurrent()) { session.mapError = error.message; mapNote(session); } },
      });
      if (!isCurrent()) { map.dispose(); return; }
      session.map = map; map.setCategory(session.geometryFilter);
      session.mapError = null; mapNote(session);
      if (session.geometryIndex) await prepareMap(session);
    } catch (error) { if (isCurrent()) { session.mapError = error.message; mapNote(session); } }
    finally { session.abort.signal.removeEventListener("abort", abort); }
  }
  async function prepareMap(session) {
    if (!session.map || !session.geometryIndex) return;
    const map = session.map;
    try { await map.refreshGeometry(session.geometryIndex); }
    catch (error) { if (session.map !== map) return; throw error; }
    if (!current(session) || session.map !== map) return;
    session.mapReady = true;
    for (const button of ui.legend.querySelectorAll("button")) button.disabled = false;
    progress(); mapNote(session);
  }
  async function run() {
    stop(); clearResults();
    const session = {abort: new AbortController(), job: null, refs: null, summary: null, geometryFilter: ""}; active = session;
    try {
      progress({phase: "queued", total: null});
      await cancellation;
      if (!current(session)) return;
      session.refs = refs(); session.mapPromise = showMap(session);
      const capabilities = await request("/api/comparisons", session);
      if (capabilities.map_index_version !== 1) throw new Error("The viewer update is still deploying. Retry this comparison shortly.");
      const expected = Object.fromEntries(["baseline", "target"].map(side => [side, comparisonFiles(session.refs[side])]));
      progress({phase: "downloading", total: null});
      // Observe superseded start responses so their server jobs can be cancelled.
      session.startPromise = request("/api/comparisons", session, {method: "POST", signal: undefined, headers: {"Content-Type": "application/json"}, body: JSON.stringify({slug: asset.slug, baseline: session.refs.baseline.date, target: session.refs.target.date, expected})});
      const result = await session.startPromise;
      if (!current(session)) return;
      session.job = result.job_id;
      let response = result;
      while (response.state === "running") {
        progress(response.progress);
        await delay(600, session.abort.signal); response = await request(`/api/comparisons/${session.job}`, session);
        if (!current(session)) return;
      }
      if (response.state !== "complete") throw new Error(response.error || "Comparison cancelled");
      session.summary = response.summary; renderSummary(response.summary);
      status(""); mapNote(session);
      session.geometryIndex = await loadMapIndex(session, value => { if (current(session)) progress(value); });
      await session.mapPromise;
      if (!current(session)) return;
      await prepareMap(session);
      if (!session.map) progress();
      session.keepalive = window.setInterval(() => {
        void request(`/api/comparisons/${session.job}`, session).catch(error => { if (current(session)) status(error.message); });
      }, 60000);
      if (response.summary.identity.compatible) await loadPage(session);
    } catch (error) { if (current(session)) { session.mapError = error.message; mapNote(session); progress(); status(error.message); } }
  }
  ui.open.addEventListener("click", () => {
    if (opened) close();
    else { setMode(true); ui.before.focus(); void run(); }
  });
  for (const side of ["before", "after"]) ui[side].addEventListener("change", () => {
    if (!opened || !ui.before.value || !ui.after.value) return;
    stop(); clearResults(); progress({phase: "downloading", total: null});
    rerunTimer = window.setTimeout(() => { rerunTimer = null; void run(); }, 120);
  });
  ui.legend.addEventListener("click", event => {
    const button = event.target.closest("button[data-change]");
    if (!button || !active?.summary) return;
    const session = active;
    session.geometryFilter = session.geometryFilter === button.dataset.change ? "" : button.dataset.change;
    for (const item of ui.legend.querySelectorAll("button")) item.setAttribute("aria-pressed", String(item.dataset.change === session.geometryFilter));
    onFeatureSelect([]);
    session.map?.setCategory(session.geometryFilter);
    if (session.summary.identity.compatible) void loadPage(session);
  });
  ui.search.addEventListener("input", () => { if (active?.summary?.identity.compatible) void loadPage(active); });
  ui.filter.addEventListener("change", () => { if (active?.summary?.identity.compatible) void loadPage(active); });
  ui.previous.addEventListener("click", () => { if (active?.page) void loadPage(active, Math.max(0, active.page.offset - 50)); });
  ui.next.addEventListener("click", () => { if (active?.page) void loadPage(active, active.page.offset + 50); });
  ui.details.addEventListener("click", () => setDetails(ui.panel.hidden));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && opened && (ui.panel.contains(document.activeElement) || ui["release-controls"].contains(document.activeElement) || document.activeElement === ui.open || document.activeElement === ui.details)) close();
  });
  return {
    isOpen: () => opened,
    refreshMap: () => { onFeatureSelect([]); if (active?.refs) active.mapPromise = showMap(active); },
    setAsset(next, selectedVersion = "latest", opts = {}) {
      if (asset === next && version === selectedVersion) return;
      stop(); clearResults(); viewport = null; if (opened) setMode(false);
      asset = next; version = selectedVersion; options = opts;
      ui.open.hidden = !asset || (asset.versions || []).length < 2;
      if (ui.open.hidden) return;
      const dates = [...asset.versions].map(v => v.date).sort().reverse();
      for (const side of ["before", "after"]) { ui[side].replaceChildren(); dates.forEach(date => ui[side].append(new Option(date, date))); }
      const after = selectedVersion === "latest" ? asset.latest_release.date : selectedVersion;
      ui.after.value = after; ui.before.value = dates[dates.indexOf(after) + 1] || after;
      ui.search.value = ""; ui.filter.value = "";
    },
  };
}
