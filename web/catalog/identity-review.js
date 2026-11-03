import {featureLinkUrl} from "./release-reference.js";

const PARAMS = ["review_case", "review_asset", "review_release", "review_execution", "review_generation", "review_size", "review_sha256"];
const GUIDE = "https://github.com/SkyTruth/shared-datasets-1/blob/main/catalog/feature-identity-resolutions/README.md";

function node(tag, text, className = "") {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function link(text, href) {
  const element = node("a", text, "quiet-link");
  element.href = href; element.target = "_blank"; element.rel = "noopener noreferrer";
  return element;
}

export function openIdentityReview({dialog, href, loadMapModule}) {
  const url = new URL(href), params = new URLSearchParams();
  const provided = [...url.searchParams.keys()].filter(key => key.startsWith("review_"));
  if (provided.length !== PARAMS.length || PARAMS.some(key => url.searchParams.getAll(key).length !== 1 || !url.searchParams.get(key))) {
    throw new Error("Review link is incomplete or contains unknown or duplicate parameters.");
  }
  for (const key of PARAMS) params.set(key, url.searchParams.get(key));
  const lifetime = new AbortController();
  let request = null;
  const status = dialog.querySelector("#identity-review-status"), content = dialog.querySelector("#identity-review-content");
  const current = signal => dialog.open && !signal.aborted && !lifetime.signal.aborted;

  async function loadCase() {
    request?.abort(); request = new AbortController();
    const signal = request.signal;
    content.hidden = true; content.replaceChildren(); status.hidden = false; status.textContent = "Loading captured evidence…";
    const captured = new URLSearchParams(params);
    try {
      const response = await fetch(`/api/identity-review?${captured}`, {cache: "no-store", credentials: "include", signal});
      if (!response.headers.get("content-type")?.includes("application/json")) {
        throw new Error(`The private evidence reader is unavailable (HTTP ${response.status}). Use the authenticated production catalog from the alert.`);
      }
      const payload = await response.json();
      if (!current(signal)) return;
      if (!response.ok) throw new Error(payload.error || `Evidence request returned HTTP ${response.status}`);
      if (payload.schema !== "identity-review-case-v1" || payload.case_id !== captured.get("review_case") ||
          payload.asset_slug !== captured.get("review_asset") || payload.release !== captured.get("review_release") ||
          payload.execution !== captured.get("review_execution") || payload.review_bundle.generation !== captured.get("review_generation") ||
          String(payload.review_bundle.size) !== captured.get("review_size") || payload.review_bundle.sha256 !== captured.get("review_sha256")) {
        throw new Error("The viewer returned a different evidence snapshot.");
      }
      renderCase(payload, signal);
      status.hidden = true; content.hidden = false;
      const mapModule = await loadMapModule();
      if (!current(signal)) return;
      const controller = await mapModule.renderIdentityReviewMap({container: content.querySelector(".identity-review-map"),
        status: content.querySelector(".identity-review-map-status"), signal, features: geometryFeatures(payload, content)});
      if (!current(signal)) return;
      for (const control of content.querySelectorAll("[data-review-map-control]")) {
        control.addEventListener("change", () => controller?.update(geometryFeatures(payload, content)), {signal});
      }
    } catch (error) {
      if (!current(signal)) return;
      if (!content.hidden) {
        content.querySelector(".identity-review-map-status").textContent = `Footprint map unavailable. ${error.message}`;
      } else {
        status.textContent = `Identity review unavailable. ${error.message} Close review to continue browsing published data.`;
      }
    }
  }

  function renderCase(payload, signal) {
    dialog.querySelector("#identity-review-title").textContent = `${payload.asset_slug} · ${payload.release}`;
    content.append(node("p", "Compare the incoming record with its previous candidates. A maintainer must decide whether they represent the same feature."));
    if (payload.sample_fraction < 1) content.append(node("p",
      `Compatibility sample (${(payload.sample_fraction * 100).toLocaleString()}%). This cannot establish the full release or prove an old source key is absent. Reusing a previous ID requires complete-source checks.`, "identity-review-warning"));
    else content.append(node("p", "Complete asset input. Check the full source and identifying changes before choosing an action.", "identity-review-scope"));
    const caseLabel = node("label", "Case "), cases = node("select"); cases.id = "identity-review-case";
    for (const item of payload.cases) { const option = node("option", item.source_key.join(" / ")); option.value = item.case_id; cases.append(option); }
    cases.value = payload.case_id; caseLabel.append(cases); content.append(caseLabel);
    cases.addEventListener("change", () => {
      params.set("review_case", cases.value); url.search = params.toString(); history.replaceState(null, "", url);
      void loadCase();
    }, {signal});
    content.append(node("p", `Incoming source key: ${payload.ambiguity.new_identity_key.join(" / ")}`));
    content.append(node("p", payload.previous_feature_id_for_key
      ? `This source key already owns published feature ID ${payload.previous_feature_id_for_key}.`
      : "This source key did not own an ID in the complete previous baseline."));
    const candidateLabel = node("label", "Compare with previous feature "), candidates = node("select");
    candidates.id = "identity-review-previous"; candidates.dataset.reviewMapControl = "true";
    for (const item of payload.previous) {
      const relation = item.feature_id === payload.previous_feature_id_for_key ? "owns this source key" :
        [item.geometry_matches ? "matching footprint" : "", item.properties_match ? "matching attributes" : ""].filter(Boolean).join(", ");
      const option = node("option", `ID ${item.feature_id} · ${relation}`); option.value = item.feature_id; candidates.append(option);
    }
    candidates.value = payload.previous_feature_id_for_key || payload.previous[0].feature_id;
    candidateLabel.append(candidates); content.append(candidateLabel);
    const comparison = node("section"); comparison.id = "identity-review-fields"; content.append(comparison);
    const showCandidate = () => {
      const before = payload.previous.find(item => item.feature_id === candidates.value);
      const reference = {slug: payload.asset_slug, date: payload.baseline.release, files: [{...payload.baseline.metadata, format: "metadata"}]};
      comparison.replaceChildren(node("h3", `Previous feature ${before.feature_id}`),
        link("Open this published feature", featureLinkUrl(reference, before.feature_id, href)),
        node("p", before.geometry_hash === payload.ambiguity.new_geometry_hash ? "Stored footprints are identical." : "Stored footprints changed."));
      if (before.key_present_in_input) comparison.append(node("p", "This previous source key is also present in the captured input; its ID cannot be reused for a different record."));
      const table = node("table"), head = node("thead"), row = node("tr"), body = node("tbody");
      for (const title of ["Field", "Previous", "Incoming"]) row.append(node("th", title));
      head.append(row); table.append(head, body);
      for (const field of before.fields) {
        const tr = node("tr", undefined, field.changed ? "changed" : "");
        tr.append(node("th", field.field));
        for (const side of [field.previous, field.incoming]) tr.append(node("td", side.display, side.present ? "" : "absent"));
        body.append(tr);
      }
      comparison.append(node("p", "Changed fields are highlighted. Absent and null values are different."), table);
    };
    candidates.addEventListener("change", showCandidate, {signal}); showCandidate();
    const mapSection = node("section"), controls = node("div", undefined, "identity-review-map-controls");
    mapSection.append(node("h3", "Stored footprints"));
    for (const [side, label] of [["incoming", "Incoming · orange"], ["previous", "Previous · blue"]]) {
      const labelElement = node("label"), check = node("input");
      check.type = "checkbox"; check.checked = true; check.id = `identity-review-show-${side}`; check.dataset.reviewMapControl = "true";
      labelElement.append(check, document.createTextNode(label)); controls.append(labelElement);
    }
    mapSection.append(controls);
    const map = node("div", undefined, "identity-review-map"), mapStatus = node("p", "Loading stored footprints…", "identity-review-map-status");
    map.append(mapStatus); mapSection.append(map); content.insertBefore(mapSection, comparison);
    const choices = node("section"); choices.append(node("h3", "Choices to review"));
    const list = node("ul");
    for (const choice of payload.choices) {
      let text;
      switch (choice.action) {
        case "keep_previous_key_mapping": text = `Keep ID ${choice.feature_ids[0]}: preserve this record's existing identity if the evidence supports continuity.`; break;
        case "reuse_previous_feature_id": text = `Reuse a previous ID (${choice.feature_ids.join(", ")}): treat the new source key as a replacement for that feature, after complete-source absence and continuity checks.`; break;
        case "assign_new_feature_id": text = "Assign a new ID: treat the incoming record as a separate feature. This intentionally starts a new identity."; break;
        default: throw new Error("Unsupported identity-review choice");
      }
      list.append(node("li", text));
    }
    choices.append(list, node("p", "These are permitted by the captured evidence, not approved decisions. Opening this page does not change data."),
      link("Read the resolution-file guide", GUIDE)); content.append(choices);
    const details = node("details"); details.append(node("summary", "Exact evidence and source versions"),
      node("pre", JSON.stringify({case_id: payload.case_id, review_bundle: payload.review_bundle, baseline: payload.baseline,
        source: payload.source, producer: payload.producer, native_versions: payload.native_versions}, null, 2)));
    content.append(details);
  }

  void loadCase();
  return () => { lifetime.abort(); request?.abort(); };
}

function geometryFeatures(payload, content) {
  const features = [], before = payload.previous.find(item => item.feature_id === content.querySelector("#identity-review-previous").value);
  for (const [side, feature] of [["incoming", payload.incoming], ["previous", before]]) {
    if (content.querySelector(`#identity-review-show-${side}`).checked && feature.geometry) {
      features.push({type: "Feature", properties: {side, feature_id: feature.feature_id || "incoming"}, geometry: feature.geometry});
    }
  }
  return {type: "FeatureCollection", features};
}
