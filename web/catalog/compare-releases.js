import {selectReleaseReference, metadataFile, releaseFile, artifactGeneration, captureArtifact} from "./release-reference.js";

export const CHANGE_LABELS = {added: "Added", removed: "Removed", geometry_only: "Geometry only", properties_only: "Properties only", both: "Geometry and properties", unchanged: "Unchanged"};
const GEOMETRY_LABELS = {novel: "New geometry", removed: "Removed geometry", metadata_changed: "Metadata changed", unchanged: "Unchanged geometry"};

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

export function createComparisonController({loadMapModule = () => import("./map-preview.js"), onModeChange = () => {}, getBasemap = () => "map"} = {}) {
  const ids = ["open", "panel", "before", "after", "release-controls", "status", "summary", "schema", "search", "filter", "rows", "previous", "next", "page", "inspector", "details", "map-note", "table", "legend"];
  const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(`compare-${id}`)]));
  const versionControl = document.getElementById("version-control"), mapContainer = document.getElementById("map-preview"), mapStatus = document.getElementById("map-status");
  let asset = null, version = "latest", options = {}, active = null, opened = false, viewport = null, pageSerial = 0, inspectSerial = 0, rerunTimer = null;
  const current = session => active === session && !session.abort.signal.aborted;
  const status = text => { ui.status.textContent = text; ui.status.hidden = !opened || !text; };
  function setDetails(expanded) {
    ui.panel.hidden = !opened || !expanded;
    ui.details.setAttribute("aria-expanded", String(expanded));
    ui.details.textContent = expanded ? "▾ Details" : "▸ Details";
  }
  function mapNote(session) {
    ui["map-note"].textContent = session.mapError ? `Map inspection unavailable: ${session.mapError}` : "Colors cover all loaded geometry, independently of table pages. Unchanged geometry is faint. Display detail varies with zoom.";
  }
  async function cancelJob(id) {
    if (!id) return;
    try { await fetch(`/api/comparisons/${id}/cancel`, {method: "POST", credentials: "include"}); }
    catch { /* A disconnected browser cannot acknowledge cancellation; server TTL/budget still applies. */ }
  }
  function stop() {
    window.clearTimeout(rerunTimer); rerunTimer = null;
    pageSerial++; inspectSerial++;
    if (active) {
      viewport = active.map?.viewport() || viewport;
      active.abort.abort(); active.mapAbort?.abort(); active.map?.dispose();
      void cancelJob(active.job); active = null;
    }
  }
  function clearResults() {
    for (const key of ["summary", "schema", "rows", "inspector"]) ui[key].replaceChildren();
    ui.table.hidden = true; ui.previous.disabled = ui.next.disabled = true;
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
    const params = new URLSearchParams({offset, limit: 50, query: ui.search.value, classification: ui.filter.value});
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
      ui.inspector.replaceChildren(table(["Property", "Before", "After"], rows, `${id} · ${CHANGE_LABELS[feature.classification]}`));
      const provenance = element("details");
      provenance.append(element("summary", "Publication provenance"), element("pre", JSON.stringify({before: feature.before?.provenance ?? null, after: feature.after?.provenance ?? null}, null, 2)));
      ui.inspector.append(provenance); session.map?.selectFeature(id);
    } catch (error) { if (current(session) && token === inspectSerial) status(error.message); }
  }
  async function showMap(session) {
    viewport = session.map?.viewport() || viewport;
    session.mapAbort?.abort(); session.map?.dispose();
    const mapAbort = new AbortController(); session.mapAbort = mapAbort;
    const abort = () => mapAbort.abort(); session.abort.signal.addEventListener("abort", abort, {once: true});
    const isCurrent = () => current(session) && session.mapAbort === mapAbort && !mapAbort.signal.aborted;
    try {
      const module = await loadMapModule(); if (!isCurrent()) return;
      const map = await module.renderComparisonMap({container: mapContainer, status: mapStatus, baseline: session.refs.baseline, target: session.refs.target,
        signal: mapAbort.signal, basemap: getBasemap(), viewport,
        onSelect: id => { if (current(session) && session.summary?.identity.compatible) void inspect(session, id); },
        lookupGeometry: async (side, ids) => (await request(`/api/comparisons/${session.job}/map`, session, {method: "POST", signal: mapAbort.signal, headers: {"Content-Type": "application/json"}, body: JSON.stringify({side, feature_ids: ids})})).map_features,
        onError: error => { if (isCurrent()) { session.mapError = error.message; mapNote(session); } },
      });
      if (!isCurrent()) { map.dispose(); return; }
      session.map = map; session.mapError = null; mapNote(session);
      if (session.summary) map.refreshGeometry();
    } catch (error) { if (isCurrent()) { session.mapError = error.message; mapNote(session); } }
    finally { session.abort.signal.removeEventListener("abort", abort); }
  }
  async function run() {
    stop(); clearResults();
    const session = {abort: new AbortController(), job: null, refs: null, summary: null}; active = session;
    try {
      session.refs = refs(); void showMap(session);
      const expected = Object.fromEntries(["baseline", "target"].map(side => [side, comparisonFiles(session.refs[side])]));
      status("Comparing releases…");
      // Observe superseded start responses so their server jobs can be cancelled.
      const result = await request("/api/comparisons", session, {method: "POST", signal: undefined, headers: {"Content-Type": "application/json"}, body: JSON.stringify({slug: asset.slug, baseline: session.refs.baseline.date, target: session.refs.target.date, expected})});
      if (!current(session)) { void cancelJob(result.job_id); return; }
      session.job = result.job_id;
      let response = result;
      while (response.state === "running") {
        status(`${response.progress.phase}: ${response.progress.rows.toLocaleString()} validated rows…`);
        await delay(600, session.abort.signal); response = await request(`/api/comparisons/${session.job}`, session);
        if (!current(session)) return;
      }
      if (response.state !== "complete") throw new Error(response.error || "Comparison cancelled");
      session.summary = response.summary; renderSummary(response.summary);
      status(""); session.map?.refreshGeometry();
      if (response.summary.identity.compatible) await loadPage(session);
    } catch (error) { if (current(session)) status(error.message); }
  }
  ui.open.addEventListener("click", () => {
    if (opened) close();
    else { setMode(true); ui.before.focus(); void run(); }
  });
  for (const side of ["before", "after"]) ui[side].addEventListener("change", () => {
    if (!opened || !ui.before.value || !ui.after.value) return;
    stop(); clearResults(); status("Comparing releases…");
    rerunTimer = window.setTimeout(() => { rerunTimer = null; void run(); }, 120);
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
    refreshMap: () => { if (active?.refs) void showMap(active); },
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
