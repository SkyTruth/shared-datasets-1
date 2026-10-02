import {selectReleaseReference, metadataFile, releaseFile, artifactGeneration, captureArtifact} from "./release-reference.js";

export const CHANGE_LABELS = {added: "Added", removed: "Removed", geometry_only: "Geometry only", properties_only: "Properties only", both: "Geometry and properties", unchanged: "Unchanged"};

export function comparisonFiles(reference) {
  const files = {metadata: metadataFile(reference.files), schema: releaseFile(reference.files, "schema"), manifest: releaseFile(reference.files, "manifest")};
  if (reference.pmtiles_file) files.pmtiles = reference.pmtiles_file;
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

function delay(ms, signal) {
  return new Promise((resolve, reject) => {
    const done = () => { signal.removeEventListener("abort", abort); resolve(); };
    const timer = setTimeout(done, ms);
    const abort = () => { clearTimeout(timer); reject(new DOMException("Cancelled", "AbortError")); };
    signal.addEventListener("abort", abort, {once: true});
  });
}

export function createComparisonController({loadMapModule = () => import("./map-preview.js")} = {}) {
  const ids = ["open", "panel", "baseline", "target", "run", "close", "status", "summary", "schema", "search", "filter", "rows", "previous", "next", "page", "inspector", "report", "maps", "map-note", "table", "mode"];
  const ui = Object.fromEntries(ids.map(id => [id, document.getElementById(`compare-${id}`)]));
  let asset = null, version = "latest", options = {}, active = null, pageSerial = 0, inspectSerial = 0;
  const current = (session) => active === session && !session.abort.signal.aborted;
  const status = (text) => { ui.status.textContent = text; };
  function mapNote(session, text = session.mapNote || "Before and after use distinct pinned releases; tile visibility varies by zoom.") {
    session.mapNote = text;
    ui["map-note"].textContent = session.mapError ? `Map inspection unavailable: ${session.mapError} ${text}` : text;
  }
  const cancelJob = async (id) => {
    if (!id) return;
    try { await fetch(`/api/comparisons/${id}/cancel`, {method: "POST", credentials: "include"}); }
    catch { /* Closing a disconnected browser cannot acknowledge server cancellation; server TTL/budget still applies. */ }
  };
  function stop() {
    pageSerial++; inspectSerial++;
    if (active) {
      active.abort.abort(); active.mapAbort?.abort(); active.map?.dispose();
      void cancelJob(active.job);
      active = null;
    }
  }
  function clearResults() {
    for (const key of ["summary", "schema", "rows", "inspector", "maps"]) ui[key].replaceChildren();
    ui.table.hidden = true;
    ui.report.disabled = true;
    ui.previous.disabled = ui.next.disabled = true;
  }
  function refs() {
    return Object.fromEntries(["baseline", "target"].map(side => [side, selectReleaseReference(asset, ui[side].value, options)]));
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
    ui.summary.replaceChildren();
    for (const side of ["baseline", "target"]) {
      const input = summary.inputs[side];
      const details = element("details");
      details.append(element("summary", `${side === "baseline" ? "Baseline" : "Target"}: ${input.release} · ${summary.feature_counts[side].toLocaleString()} features`));
      details.append(element("pre", JSON.stringify(input.files, null, 2)));
      ui.summary.append(details);
    }
    ui.summary.append(element("p", summary.identity.reason));
    if (summary.counts) {
      const grid = element("dl", undefined, "compare-counts");
      for (const [key, label] of Object.entries(CHANGE_LABELS)) {
        const group = element("div"); group.append(element("dt", label), element("dd", summary.counts[key].toLocaleString())); grid.append(group);
      }
      ui.summary.append(grid);
    } else ui.summary.append(element("p", "Feature classifications withheld. Inspect the maps visually; numeric IDs are not joined across these releases."));
    ui.schema.replaceChildren(element("h4", "Schema changes"));
    for (const [key, label] of Object.entries({added: "Added fields", removed: "Removed fields", datatype_changes: "Datatype changes", field_semantics_changes: "Field semantics changes"})) {
      ui.schema.append(element("p", `${label}: ${summary.schema_changes[key].length}`));
      if (summary.schema_changes[key].length) ui.schema.append(element("pre", JSON.stringify(summary.schema_changes[key], null, 2)));
    }
    ui.schema.append(element("p", "Source-language properties are compared. Publication provenance and localized display values are separate from source changes."));
  }
  async function loadPage(session, offset = 0) {
    const token = ++pageSerial;
    inspectSerial++;
    ui.inspector.replaceChildren();
    const params = new URLSearchParams({offset, limit: 50, query: ui.search.value, classification: ui.filter.value});
    try {
      const result = await request(`/api/comparisons/${session.job}?${params}`, session);
      if (!current(session) || token !== pageSerial) return;
      const page = result.page;
      ui.table.hidden = false;
      session.page = page;
      ui.rows.replaceChildren();
      for (const row of page.rows) {
        const tr = element("tr"), td = element("td"), button = element("button", row.feature_id, "icon-button");
        button.type = "button"; button.addEventListener("click", () => inspect(session, row.feature_id));
        td.append(button); tr.append(td, element("td", CHANGE_LABELS[row.classification])); ui.rows.append(tr);
      }
      ui.page.textContent = page.total ? `${page.offset + 1}–${Math.min(page.offset + page.limit, page.total)} of ${page.total}` : "No matching features";
      ui.previous.disabled = page.offset === 0; ui.next.disabled = page.offset + page.limit >= page.total;
      session.highlightRows = page.rows; session.map?.highlight(page.rows);
      mapNote(session, "Union of both releases: novel geometry green, removed geometry red, identical geometry with changed source metadata yellow. Highlights cover this page only; other geometry is gray. Counts use complete sidecars.");
    } catch (error) { if (current(session) && token === pageSerial) status(error.message); }
  }
  async function inspect(session, id) {
    const token = ++inspectSerial;
    try {
      const result = await request(`/api/comparisons/${session.job}?feature_id=${encodeURIComponent(id)}`, session);
      if (!current(session) || token !== inspectSerial) return;
      const feature = result.feature;
      ui.inspector.replaceChildren(element("h4", `${id} · ${CHANGE_LABELS[feature.classification]}`));
      const table = element("table"), head = element("tr");
      ["Property", "Before", "After"].forEach(text => head.append(element("th", text))); table.append(head);
      const a = feature.before?.properties || {}, b = feature.after?.properties || {};
      for (const field of [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()) {
        const tr = element("tr");
        const value = props => Object.hasOwn(props, field) ? JSON.stringify(props[field]) : "Absent";
        tr.append(element("th", field), element("td", value(a)), element("td", value(b))); table.append(tr);
      }
      ui.inspector.append(table);
      const provenance = element("details");
      provenance.append(element("summary", "Publication provenance"), element("pre", JSON.stringify({before: feature.before?.provenance ?? null, after: feature.after?.provenance ?? null}, null, 2)));
      ui.inspector.append(provenance);
      session.highlightRows = [feature]; session.map?.highlight([feature]);
      mapNote(session, `Only selected feature ${id} is highlighted. Before and after use distinct pinned releases; tile visibility varies by zoom.`);
    } catch (error) { if (current(session) && token === inspectSerial) status(error.message); }
  }
  async function showMaps(session) {
    session.mapAbort?.abort(); session.map?.dispose();
    const mapAbort = new AbortController(); session.mapAbort = mapAbort;
    const abort = () => mapAbort.abort();
    session.abort.signal.addEventListener("abort", abort, {once: true});
    const isCurrent = () => current(session) && session.mapAbort === mapAbort && !mapAbort.signal.aborted;
    try {
      const module = await loadMapModule();
      if (!isCurrent()) return;
      const map = await module.renderComparisonMaps({container: ui.maps, baseline: session.refs.baseline, target: session.refs.target,
        signal: mapAbort.signal, mode: ui.mode.value, onSelect: id => { if (session.summary?.identity.compatible) void inspect(session, id); }});
      if (!isCurrent()) { map?.dispose(); return; }
      session.map = map;
      session.mapError = null; mapNote(session);
      session.map.highlight(session.highlightRows || []);
    } catch (error) { if (isCurrent()) { session.mapError = error.message; mapNote(session); } }
    finally { session.abort.signal.removeEventListener("abort", abort); }
  }
  async function run() {
    stop(); clearResults();
    const session = {abort: new AbortController(), job: null, refs: null, summary: null}; active = session;
    ui.run.textContent = "Cancel comparison";
    try {
      session.refs = refs();
      const expected = Object.fromEntries(["baseline", "target"].map(side => [side, comparisonFiles(session.refs[side])]));
      status("Starting comparison…");
      // Keep the start response observable so a superseded start can cancel its server job.
      const result = await request("/api/comparisons", session, {method: "POST", signal: undefined, headers: {"Content-Type": "application/json"}, body: JSON.stringify({slug: asset.slug, baseline: session.refs.baseline.date, target: session.refs.target.date, expected})});
      if (!current(session)) { void cancelJob(result.job_id); return; }
      session.job = result.job_id;
      void showMaps(session);
      let response = result;
      while (response.state === "running") {
        status(`${response.progress.phase}: ${response.progress.rows.toLocaleString()} validated rows…`);
        await delay(600, session.abort.signal);
        response = await request(`/api/comparisons/${session.job}`, session);
        if (!current(session)) return;
      }
      if (response.state !== "complete") throw new Error(response.error || "Comparison cancelled");
      session.summary = response.summary;
      renderSummary(response.summary);
      ui.report.disabled = false;
      status("Comparison complete. Exact input paths and generations are listed below.");
      if (response.summary.identity.compatible) await loadPage(session);
    } catch (error) {
      if (!current(session)) return;
      status(error.message);
      if (session.refs && !session.map && !session.job) void showMaps(session);
    } finally { if (current(session)) ui.run.textContent = "Compare"; }
  }
  ui.open.addEventListener("click", () => {
    ui.panel.hidden = false; ui.open.setAttribute("aria-expanded", "true"); ui.baseline.focus();
    status("Choose published releases. Comparison requires the authenticated viewer; visual inspection and the local CLI remain available.");
  });
  ui.close.addEventListener("click", () => { stop(); ui.panel.hidden = true; ui.open.setAttribute("aria-expanded", "false"); ui.open.focus(); });
  ui.run.addEventListener("click", () => {
    if (ui.run.textContent === "Cancel comparison") { stop(); clearResults(); status("Comparison cancelled."); ui.run.textContent = "Compare"; }
    else void run();
  });
  for (const side of ["baseline", "target"]) ui[side].addEventListener("change", () => { stop(); clearResults(); ui.run.textContent = "Compare"; status("Release selection changed. Run comparison for these releases."); });
  ui.mode.addEventListener("change", () => {
    if (active?.refs) { active.map?.dispose(); active.map = null; void showMaps(active); }
  });
  ui.search.addEventListener("input", () => { if (active?.summary?.identity.compatible) void loadPage(active); });
  ui.filter.addEventListener("change", () => { if (active?.summary?.identity.compatible) void loadPage(active); });
  ui.previous.addEventListener("click", () => { if (active?.page) void loadPage(active, Math.max(0, active.page.offset - 50)); });
  ui.next.addEventListener("click", () => { if (active?.page) void loadPage(active, active.page.offset + 50); });
  ui.report.addEventListener("click", async () => {
    const session = active;
    if (!session?.job) return;
    try {
      const response = await fetch(`/api/comparisons/${session.job}/report`, {credentials: "include", signal: session.abort.signal, cache: "no-store"});
      if (!response.ok) throw new Error((await response.json()).error || "Report unavailable");
      const payload = await response.json(); assertComparisonInputs(payload.summary.inputs, session.refs);
      if (!current(session)) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: "application/json"}));
      const link = element("a"); link.href = url; link.download = `${asset.slug}-comparison.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (current(session)) status(error.message); }
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !ui.panel.hidden && ui.panel.contains(document.activeElement)) ui.close.click(); });
  return {
    setAsset(next, selectedVersion = "latest", opts = {}) {
      if (asset === next && version === selectedVersion) return;
      stop(); clearResults(); asset = next; version = selectedVersion; options = opts;
      ui.panel.hidden = true; ui.open.setAttribute("aria-expanded", "false");
      ui.open.disabled = !asset || (asset.versions || []).length < 2;
      ui.open.title = ui.open.disabled ? "Two published releases are required" : "Compare published release features";
      if (ui.open.disabled) return;
      const dates = [...asset.versions].map(v => v.date).sort().reverse();
      for (const side of ["baseline", "target"]) { ui[side].replaceChildren(); dates.forEach(date => ui[side].append(new Option(date, date))); }
      const target = selectedVersion === "latest" ? asset.latest_release.date : selectedVersion;
      const index = dates.indexOf(target);
      ui.target.value = target; ui.baseline.value = dates[index + 1] || target;
      ui.search.value = ""; ui.filter.value = ""; ui.run.textContent = "Compare";
    },
  };
}
