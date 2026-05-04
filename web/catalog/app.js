const state = {
  catalog: null,
  assets: [],
  filtered: [],
  selectedSlug: null,
  selectedSlugs: [],
  mapModule: null,
  basemap: "map",
  versionBySlug: {},
  layerByReference: {},
  layerOptionsByReference: {},
  colorFieldByReference: {},
  colorFieldsByReference: {},
  docsRequestSerial: 0,
};

const elements = {
  count: document.querySelector("#catalog-count"),
  list: document.querySelector("#asset-list"),
  template: document.querySelector("#asset-card-template"),
  search: document.querySelector("#search-input"),
  category: document.querySelector("#category-filter"),
  format: document.querySelector("#format-filter"),
  cadence: document.querySelector("#cadence-filter"),
  status: document.querySelector("#status-filter"),
  accessTier: document.querySelector("#access-tier-filter"),
  empty: document.querySelector("#detail-empty"),
  detail: document.querySelector("#detail-view"),
  taxonomy: document.querySelector("#detail-taxonomy"),
  title: document.querySelector("#detail-title"),
  description: document.querySelector("#detail-description"),
  selectionLegend: document.querySelector("#selection-legend"),
  docs: document.querySelector("#detail-docs"),
  licenseNote: document.querySelector("#detail-license-note"),
  updated: document.querySelector("#detail-updated"),
  lastRunCard: document.querySelector("#detail-last-run-card"),
  lastRun: document.querySelector("#detail-last-run"),
  cadenceValue: document.querySelector("#detail-cadence"),
  owner: document.querySelector("#detail-owner"),
  statusValue: document.querySelector("#detail-status"),
  accessTierValue: document.querySelector("#detail-access-tier"),
  geometryCard: document.querySelector("#detail-geometry-card"),
  geometry: document.querySelector("#detail-geometry"),
  rowCountCard: document.querySelector("#detail-row-count-card"),
  rowCount: document.querySelector("#detail-row-count"),
  boundsCard: document.querySelector("#detail-bounds-card"),
  bounds: document.querySelector("#detail-bounds"),
  gs: document.querySelector("#detail-gs"),
  url: document.querySelector("#detail-url"),
  versionRow: document.querySelector("#version-path-row"),
  versionSelect: document.querySelector("#version-select"),
  pmtiles: document.querySelector("#detail-pmtiles"),
  pmtilesRow: document.querySelector("#pmtiles-path-row"),
  source: document.querySelector("#detail-source"),
  sourceUrlRow: document.querySelector("#detail-source-url-row"),
  sourceUrl: document.querySelector("#detail-source-url"),
  licenseText: document.querySelector("#detail-license-text"),
  mapSection: document.querySelector("#map-section"),
  mapStatus: document.querySelector("#map-status"),
  colorLegend: document.querySelector("#color-legend"),
  featureInspector: document.querySelector("#feature-inspector"),
  basemap: document.querySelector("#basemap-select"),
  colorizeControl: document.querySelector("#colorize-control"),
  colorize: document.querySelector("#colorize-select"),
  layerControl: document.querySelector("#layer-control"),
  layer: document.querySelector("#layer-select"),
  copyGs: document.querySelector("#copy-gs"),
  copyUrl: document.querySelector("#copy-url"),
  copyPmtiles: document.querySelector("#copy-pmtiles"),
  metaGrid: document.querySelector(".meta-grid"),
  pathSection: document.querySelector(".path-section"),
  sourceSection: document.querySelector(".source-section"),
  docsViewer: document.querySelector("#docs-viewer"),
  docsTitle: document.querySelector("#docs-title"),
  docsCopyMarkdown: document.querySelector("#docs-copy-markdown"),
  docsBody: document.querySelector("#docs-body"),
  docsClose: document.querySelector("#docs-close"),
};

const collator = new Intl.Collator("en", { sensitivity: "base" });
const RELEASE_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

async function init() {
  try {
    const response = await fetch(cacheBustedUrl("./catalog.json", Date.now()), { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`catalog.json returned HTTP ${response.status}`);
    }
    state.catalog = await response.json();
    state.assets = Array.isArray(state.catalog.assets) ? state.catalog.assets : [];
    await hydrateReleaseIndexes();
    state.filtered = state.assets;
    state.basemap = "map";
    elements.basemap.value = state.basemap;
    populateFilters();
    wireEvents();
    applyFilters();
  } catch (error) {
    renderFatalError(error);
  }
}

async function hydrateReleaseIndexes() {
  await Promise.allSettled(state.assets.map((asset) => hydrateReleaseIndex(asset)));
}

async function hydrateReleaseIndex(asset) {
  const url = releaseIndexUrl(asset);
  if (!url) return false;
  try {
    const response = await fetch(cacheBustedUrl(url, Date.now()), { cache: "no-store" });
    if (response.status === 404) return false;
    if (!response.ok) {
      throw new Error(`release index returned HTTP ${response.status}`);
    }
    return applyReleaseIndex(asset, await response.json());
  } catch (error) {
    console.warn(`Could not load release index for ${asset.slug}:`, error);
    return false;
  }
}

function releaseIndexUrl(asset) {
  return asset.release_index_url || `../releases/${encodeURIComponent(asset.slug)}.json`;
}

function applyReleaseIndex(asset, releaseIndex) {
  if (!releaseIndex || releaseIndex.asset_slug !== asset.slug) return false;
  const versions = versionsFromReleaseIndex(asset, releaseIndex);

  if (versions.length) {
    asset.versions = versions;
  }
  asset.latest_release = releaseIndex.latest_release || versions[0];
  asset.latest_run = releaseIndex.latest_run || null;
  asset.release_index_updated_at = releaseIndex.updated_at || "";
  const latestVersion = versions.find((version) => version.date === asset.latest_release?.date) || versions[0];
  if (latestVersion) {
    asset.canonical_sha256 = latestVersion.canonical_sha256 || "";
    asset.pmtiles_sha256 = latestVersion.pmtiles_sha256 || "";
  }
  if (asset.latest_release?.date) {
    asset.last_updated = asset.latest_release.date;
  }
  return Boolean(versions.length || asset.latest_run);
}

function versionsFromReleaseIndex(asset, releaseIndex) {
  const releases = Array.isArray(releaseIndex.releases) ? releaseIndex.releases : [];
  const versions = [];
  const seenDates = new Set();

  for (const release of releases) {
    const date = String(release?.date || "").trim();
    if (!RELEASE_DATE_RE.test(date) || seenDates.has(date)) continue;
    const files = Array.isArray(release.files) ? release.files : [];
    const canonicalFile = releaseFileForFormat(files, asset.canonical_format, asset.canonical_path) || files[0];
    const canonicalPath = releaseFilePath(canonicalFile);
    if (!canonicalPath) continue;

    const pmtilesFile = releaseFileForFormat(files, "pmtiles", asset.pmtiles_path);
    const pmtilesPath = releaseFilePath(pmtilesFile);
    const canonicalSha256 = releaseFileSha256(canonicalFile);
    const pmtilesSha256 = releaseFileSha256(pmtilesFile);

    seenDates.add(date);
    versions.push({
      date,
      canonical_path: canonicalPath,
      public_url: gsToHttps(canonicalPath),
      pmtiles_path: pmtilesPath || null,
      pmtiles_url: pmtilesPath ? gsToHttps(pmtilesPath) : null,
      available_formats: releaseFormats(asset, files),
      source_version: release.source_version || "",
      rows: release.rows ?? null,
      release_path: release.release_path || "",
      run_record_path: release.run_record_path || "",
      canonical_sha256: canonicalSha256 || "",
      pmtiles_sha256: pmtilesSha256 || "",
    });
  }

  return versions.sort((left, right) => right.date.localeCompare(left.date));
}

function releaseFileForFormat(files, formatName, preferredPath = "") {
  const format = String(formatName || "").trim();
  if (!format) return null;
  const preferredName = basename(preferredPath);
  if (preferredName) {
    const exact = files.find(
      (file) => String(file?.format || "").trim() === format && releaseFilePath(file).endsWith(`/${preferredName}`)
    );
    if (exact) return exact;
  }
  return files.find((file) => String(file?.format || "").trim() === format) || null;
}

function releaseFilePath(file) {
  const path = String(file?.path || "").trim();
  return path.startsWith("gs://") ? path : "";
}

function releaseFileSha256(file) {
  const value = String(file?.sha256 || "").trim();
  return /^[a-f0-9]{64}$/i.test(value) ? value : "";
}

function releaseFormats(asset, files) {
  const fileFormats = new Set(files.map((file) => String(file?.format || "").trim()).filter(Boolean));
  const ordered = (Array.isArray(asset.available_formats) ? asset.available_formats : []).filter((format) =>
    fileFormats.has(format)
  );
  if (!ordered.length && asset.canonical_format) {
    ordered.push(asset.canonical_format);
  }
  return ordered;
}

function basename(path) {
  return String(path || "").split("/").filter(Boolean).pop() || "";
}

function gsToHttps(path) {
  const match = String(path || "").match(/^gs:\/\/([^/]+)\/(.+)$/);
  return match ? `https://storage.googleapis.com/${match[1]}/${match[2]}` : path;
}

function wireEvents() {
  elements.search.addEventListener("input", applyFilters);
  for (const select of [elements.category, elements.format, elements.cadence, elements.status, elements.accessTier]) {
    select.addEventListener("change", applyFilters);
  }
  elements.copyGs.addEventListener("click", () => copyValue(elements.gs.textContent, elements.copyGs));
  elements.copyUrl.addEventListener("click", () => copyValue(elements.url.textContent, elements.copyUrl));
  elements.copyPmtiles.addEventListener("click", () => copyValue(elements.pmtiles.textContent, elements.copyPmtiles));
  elements.docsCopyMarkdown.addEventListener("click", () =>
    copyValue(elements.docsCopyMarkdown.dataset.markdown || "", elements.docsCopyMarkdown)
  );
  elements.docs.addEventListener("click", (event) => {
    event.preventDefault();
    const asset = state.assets.find((candidate) => candidate.slug === state.selectedSlug);
    if (asset) {
      openDocs(asset);
    }
  });
  elements.docsClose.addEventListener("click", closeDocs);
  for (const closer of document.querySelectorAll("[data-docs-close]")) {
    closer.addEventListener("click", closeDocs);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (!elements.docsViewer.hidden) {
        closeDocs();
      }
      clearFeatureInspector();
    }
  });
  elements.versionSelect.addEventListener("change", () => {
    const asset = state.assets.find((candidate) => candidate.slug === state.selectedSlug);
    if (!asset) return;
    state.versionBySlug[asset.slug] = elements.versionSelect.value;
    renderSelection();
  });
  elements.basemap.addEventListener("change", () => {
    state.basemap = elements.basemap.value === "satellite" ? "satellite" : "map";
    renderSelectedPmtiles();
  });
  elements.colorize.addEventListener("change", () => {
    const asset = selectedColorizeAsset();
    if (!asset) return;
    state.colorFieldByReference[colorReferenceKey(asset)] = elements.colorize.value;
    clearColorLegend();
    clearFeatureInspector();
    if (state.mapModule?.setColorizeField) {
      state.mapModule.setColorizeField(elements.colorize.value);
    }
  });
  elements.layer.addEventListener("change", () => {
    const asset = selectedLayerAsset();
    if (!asset) return;
    state.layerByReference[mapReferenceKey(asset)] = elements.layer.value;
    clearColorLegend();
    clearFeatureInspector();
    renderSelectedPmtiles();
  });
}

function populateFilters() {
  setOptions(elements.category, "All categories", unique(state.assets.map((asset) => asset.category)));
  setOptions(elements.format, "All formats", unique(state.assets.flatMap((asset) => asset.available_formats)));
  setOptions(elements.cadence, "All cadences", unique(state.assets.map((asset) => asset.update_cadence)));
  setOptions(elements.status, "All statuses", unique(state.assets.map((asset) => asset.status)));
  setOptions(elements.accessTier, "All access tiers", unique(state.assets.map((asset) => asset.access_tier)));
}

function setOptions(select, label, values) {
  select.replaceChildren();
  select.append(new Option(label, ""));
  for (const value of values.sort(collator.compare)) {
    select.append(new Option(value, value));
  }
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function applyFilters() {
  const query = normalize(elements.search.value);
  const category = elements.category.value;
  const format = elements.format.value;
  const cadence = elements.cadence.value;
  const status = elements.status.value;
  const accessTier = elements.accessTier.value;

  state.filtered = state.assets.filter((asset) => {
    if (category && asset.category !== category) return false;
    if (format && !asset.available_formats.includes(format)) return false;
    if (cadence && asset.update_cadence !== cadence) return false;
    if (status && asset.status !== status) return false;
    if (accessTier && asset.access_tier !== accessTier) return false;
    if (!query) return true;
    return searchableText(asset).includes(query);
  });

  renderList();
  updateCount();

  const filteredSlugs = new Set(state.filtered.map((asset) => asset.slug));
  state.selectedSlugs = state.selectedSlugs.filter((slug) => filteredSlugs.has(slug));
  if (state.selectedSlug && !filteredSlugs.has(state.selectedSlug)) {
    state.selectedSlug = state.selectedSlugs[state.selectedSlugs.length - 1] || null;
  }

  if (!state.selectedSlugs.length) {
    const firstVisible = orderedAssetsForList(state.filtered)[0];
    if (firstVisible) {
      selectAsset(firstVisible.slug, { scroll: false });
    } else {
      clearDetail();
    }
  } else {
    renderSelection();
    markSelected();
  }
}

function searchableText(asset) {
  return normalize(
    [
      asset.slug,
      asset.title,
      asset.category,
      asset.subcategory,
      asset.access_tier,
      asset.description,
      asset.geometry_type,
      formatRowCount(asset.row_count),
      formatBounds(asset.bounds),
      asset.source,
      asset.source_url,
      asset.license,
      Array.isArray(asset.license_flags) ? asset.license_flags.join(" ") : "",
      asset.latest_release?.date,
      asset.latest_run?.date,
      asset.latest_run?.status,
      asset.notes,
      asset.available_formats.join(" "),
    ].join(" ")
  );
}

function normalize(value) {
  return String(value || "").toLowerCase().trim();
}

function renderList() {
  elements.list.replaceChildren();
  if (!state.filtered.length) {
    const message = document.createElement("div");
    message.className = "error-state";
    message.textContent = "No datasets match the active search and filters.";
    elements.list.append(message);
    return;
  }

  const fragment = document.createDocumentFragment();
  const selectedSet = new Set(state.selectedSlugs);
  let currentGroup = "";
  for (const asset of orderedAssetsForList(state.filtered)) {
    const group = subcategoryGroupKey(asset);
    if (group !== currentGroup) {
      currentGroup = group;
      fragment.append(renderSubcategoryHeading(asset));
    }
    fragment.append(renderAssetCard(asset, selectedSet));
  }
  elements.list.append(fragment);
}

function orderedAssetsForList(assets) {
  return [...assets].sort((left, right) => {
    const category = collator.compare(left.category || "", right.category || "");
    if (category) return category;
    const subcategory = collator.compare(left.subcategory || "", right.subcategory || "");
    if (subcategory) return subcategory;
    const title = collator.compare(left.title || "", right.title || "");
    if (title) return title;
    return collator.compare(left.slug || "", right.slug || "");
  });
}

function subcategoryGroupKey(asset) {
  return `${asset.category || ""}/${asset.subcategory || ""}`;
}

function renderSubcategoryHeading(asset) {
  const heading = document.createElement("div");
  heading.className = "asset-group-heading";
  const subcategory = document.createElement("span");
  subcategory.textContent = asset.subcategory || "Uncategorized";
  const category = document.createElement("em");
  category.textContent = asset.category || "";
  heading.append(subcategory, category);
  return heading;
}

function renderAssetCard(asset, selectedSet) {
  const node = elements.template.content.firstElementChild.cloneNode(true);
  node.dataset.slug = asset.slug;
  node.setAttribute("aria-selected", selectedSet.has(asset.slug) ? "true" : "false");
  node.style.setProperty("--selection-color", colorForSlug(asset.slug));
  node.querySelector(".asset-title").textContent = asset.title;
  node.querySelector(".asset-summary").textContent = asset.description || asset.notes || asset.source;
  node.querySelector(".asset-meta").textContent = `${asset.category} / ${asset.subcategory}`;
  node.querySelector(".asset-date").textContent = asset.latest_release?.date || asset.last_updated || "No release";
  const formats = node.querySelector(".asset-formats");
  for (const format of asset.available_formats) {
    const chip = document.createElement("span");
    chip.textContent = format;
    formats.append(chip);
  }
  node.addEventListener("click", (event) => {
    selectAsset(asset.slug, { additive: event.metaKey || event.ctrlKey });
  });
  return node;
}

function updateCount() {
  const total = state.assets.length;
  const shown = state.filtered.length;
  elements.count.textContent = `${shown} of ${total} assets`;
}

function selectAsset(slug, options = {}) {
  const asset = state.assets.find((candidate) => candidate.slug === slug);
  if (!asset) return;
  state.selectedSlug = slug;
  if (options.additive) {
    const selected = new Set(state.selectedSlugs);
    if (selected.has(slug)) {
      selected.delete(slug);
    } else {
      selected.add(slug);
    }
    state.selectedSlugs = [...selected];
    state.selectedSlug = state.selectedSlugs[state.selectedSlugs.length - 1] || null;
  } else {
    state.selectedSlugs = [slug];
  }
  renderSelection();
  markSelected();
  if (options.scroll !== false) {
    document.querySelector(".detail-panel").scrollTo({ top: 0, behavior: "smooth" });
  }
}

function markSelected() {
  const selected = new Set(state.selectedSlugs);
  for (const card of elements.list.querySelectorAll(".asset-card")) {
    card.setAttribute("aria-selected", selected.has(card.dataset.slug) ? "true" : "false");
    card.style.setProperty("--selection-color", colorForSlug(card.dataset.slug));
  }
}

function renderSelection() {
  const assets = selectedAssets();
  if (!assets.length) {
    clearDetail();
    return;
  }
  if (assets.length === 1) {
    renderDetail(assets[0]);
  } else {
    renderMultiDetail(assets);
  }
}

function selectedAssets() {
  return state.selectedSlugs
    .map((slug) => state.assets.find((asset) => asset.slug === slug))
    .filter(Boolean);
}

function renderDetail(asset) {
  elements.empty.hidden = true;
  elements.detail.hidden = false;
  elements.detail.classList.remove("multi-detail");
  elements.docs.hidden = false;
  elements.metaGrid.hidden = false;
  elements.pathSection.hidden = false;
  elements.sourceSection.hidden = false;
  renderSelectionLegend([]);
  elements.taxonomy.textContent = `${asset.category} / ${asset.subcategory}`;
  elements.title.textContent = asset.title;
  elements.description.textContent = asset.description || asset.notes || "No description is available yet.";
  elements.docs.href = asset.docs_url;
  elements.updated.textContent = asset.latest_release?.date || asset.last_updated || "Unknown";
  renderLastRun(asset);
  elements.cadenceValue.textContent = asset.update_cadence || "Unknown";
  elements.owner.textContent = asset.owner || "Unknown";
  elements.statusValue.textContent = asset.status || "Unknown";
  elements.accessTierValue.textContent = asset.access_tier || "Unknown";
  elements.source.textContent = asset.source || "Unknown";
  elements.licenseText.textContent = asset.license || "Unknown";
  renderDiscoveryMetadata(asset);
  renderSourceUrl(asset);
  renderVersionSelector(asset);
  const reference = selectedReference(asset);
  elements.gs.textContent = reference.canonical_path;
  elements.url.textContent = reference.public_url;
  renderLicenseNote(asset);
  renderPmtiles([reference]);
}

function renderMultiDetail(assets) {
  const mapAssets = selectedReferences(assets).filter((asset) => asset.pmtiles_url);
  elements.empty.hidden = true;
  elements.detail.hidden = false;
  elements.detail.classList.add("multi-detail");
  elements.docs.hidden = true;
  elements.metaGrid.hidden = true;
  elements.pathSection.hidden = true;
  elements.sourceSection.hidden = true;
  elements.taxonomy.textContent = "Map comparison";
  elements.title.textContent = `${assets.length} datasets selected`;
  elements.description.textContent =
    mapAssets.length === assets.length
      ? "Rendering selected map-ready datasets together. Cmd-click rows to add or remove datasets."
      : `Rendering ${mapAssets.length} of ${assets.length} selected datasets with PMTiles previews. Cmd-click rows to add or remove datasets.`;
  renderVersionSelector({ versions: [] });
  renderSelectionLegend(assets);
  renderPmtiles(mapAssets);
}

function renderLastRun(asset) {
  const value = formatLatestRun(asset.latest_run);
  elements.lastRunCard.hidden = !value;
  elements.lastRun.textContent = value || "";
}

function formatLatestRun(run) {
  if (!run || typeof run !== "object") return "";
  const date = String(run.date || "").trim();
  if (!date) return "";
  const status = String(run.status || "").trim();
  return status ? `${date} (${status})` : date;
}

function selectedReferences(assets = selectedAssets()) {
  return assets.map(selectedReference);
}

function selectedMapReferences() {
  return selectedReferences().filter((asset) => asset.pmtiles_url);
}

function renderVersionSelector(asset) {
  const versions = Array.isArray(asset.versions) ? asset.versions : [];
  if (!versions.length) {
    elements.versionRow.hidden = true;
    elements.versionSelect.replaceChildren();
    return;
  }

  elements.versionRow.hidden = false;
  elements.versionSelect.replaceChildren();
  const latestDate = asset.latest_release?.date || asset.last_updated;
  const latestLabel = latestDate ? `Latest (${latestDate})` : "Latest";
  elements.versionSelect.append(new Option(latestLabel, "latest"));
  for (const version of versions) {
    elements.versionSelect.append(new Option(version.date, version.date));
  }
  elements.versionSelect.value = selectedVersionValue(asset);
}

function selectedVersionValue(asset) {
  const versions = Array.isArray(asset.versions) ? asset.versions : [];
  const saved = state.versionBySlug[asset.slug];
  if (saved === "latest" || versions.some((version) => version.date === saved)) {
    return saved;
  }
  return "latest";
}

function selectedReference(asset) {
  const selected = selectedVersionValue(asset);
  if (selected === "latest") {
    return asset;
  }
  const version = asset.versions.find((candidate) => candidate.date === selected);
  return version ? { ...asset, ...version } : asset;
}

function renderSelectionLegend(assets) {
  elements.selectionLegend.replaceChildren();
  if (!assets.length) {
    elements.selectionLegend.hidden = true;
    return;
  }

  const mapAssets = selectedReferences(assets).filter((asset) => asset.pmtiles_url);
  for (const asset of assets) {
    const reference = selectedReference(asset);
    const item = document.createElement("span");
    item.className = "selection-legend-item";
    const swatch = document.createElement("span");
    swatch.className = "selection-swatch";
    swatch.style.background = colorForSlug(asset.slug, mapAssets);
    const label = document.createElement("span");
    label.textContent = asset.title;
    item.append(swatch, label);
    if (!reference.pmtiles_url) {
      const note = document.createElement("em");
      note.textContent = "No map";
      item.append(note);
    }
    elements.selectionLegend.append(item);
  }
  elements.selectionLegend.hidden = false;
}

function colorForSlug(slug, mapAssets = selectedMapReferences()) {
  const index = mapAssets.findIndex((asset) => asset.slug === slug);
  if (index === -1) {
    return "#8b938d";
  }
  return datasetColor(index);
}

function datasetColor(index) {
  const colors =
    state.basemap === "satellite"
      ? ["#00d6ff", "#ffd84d", "#ff7bb7", "#78ff8e", "#ff9d42", "#caa7ff"]
      : ["#d84f2a", "#1f6fb2", "#8b4ec6", "#b88700", "#16715d", "#9a4261"];
  return colors[index % colors.length];
}

function renderLicenseNote(asset) {
  const flags = Array.isArray(asset.license_flags) ? asset.license_flags : [];
  const actionableFlags = flags.filter((flag) => flag !== "open");
  if (!actionableFlags.length) {
    elements.licenseNote.hidden = true;
    elements.licenseNote.textContent = "";
    return;
  }
  elements.licenseNote.hidden = false;
  elements.licenseNote.textContent = `Reuse limits: ${actionableFlags.join(", ")}.`;
}

function renderDiscoveryMetadata(asset) {
  setOptionalMeta(elements.geometryCard, elements.geometry, asset.geometry_type);
  setOptionalMeta(elements.rowCountCard, elements.rowCount, formatRowCount(asset.row_count));
  setOptionalMeta(elements.boundsCard, elements.bounds, formatBounds(asset.bounds));
}

function setOptionalMeta(card, valueElement, value) {
  const text = String(value || "").trim();
  card.hidden = !text;
  valueElement.textContent = text;
}

function formatRowCount(value) {
  if (value === null || value === undefined || value === "") {
    return "";
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? new Intl.NumberFormat("en").format(numeric) : String(value);
}

function formatBounds(bounds) {
  if (!Array.isArray(bounds) || bounds.length !== 4) {
    return "";
  }
  return bounds.map((value) => Number(value).toFixed(4)).join(", ");
}

function renderSourceUrl(asset) {
  if (!asset.source_url) {
    elements.sourceUrlRow.hidden = true;
    elements.sourceUrl.removeAttribute("href");
    elements.sourceUrl.textContent = "";
    return;
  }
  elements.sourceUrlRow.hidden = false;
  elements.sourceUrl.href = asset.source_url;
  elements.sourceUrl.textContent = asset.source_url;
}

function renderSelectedPmtiles() {
  const assets = selectedAssets();
  if (!assets.length) {
    return;
  }
  renderSelectionLegend(assets.length > 1 ? assets : []);
  renderPmtiles(selectedReferences(assets).filter((asset) => asset.pmtiles_url));
}

async function renderPmtiles(assets) {
  const rawMapAssets = (Array.isArray(assets) ? assets : [assets]).filter((asset) => asset?.pmtiles_url);
  if (!rawMapAssets.length) {
    elements.pmtilesRow.hidden = true;
    elements.mapSection.hidden = true;
    resetColorizeControl();
    resetLayerControl();
    clearColorLegend();
    clearFeatureInspector();
    return;
  }

  elements.pmtilesRow.hidden = rawMapAssets.length !== 1 || state.selectedSlugs.length !== 1;
  if (!elements.pmtilesRow.hidden) {
    elements.pmtiles.textContent = rawMapAssets[0].pmtiles_url;
  }
  const mapAssets = rawMapAssets.map(withPmtilesCacheBust);
  const layerAsset = selectedLayerAsset(rawMapAssets);
  const selectedLayer = prepareLayerControl(layerAsset);
  const colorizeAsset = selectedColorizeAsset(rawMapAssets);
  const colorField = prepareColorizeControl(colorizeAsset);
  elements.mapSection.hidden = false;
  elements.mapStatus.textContent = mapAssets.length === 1 ? "Loading map..." : `Loading ${mapAssets.length} maps...`;
  clearColorLegend();
  clearFeatureInspector();

  try {
    if (!state.mapModule) {
      const version = encodeURIComponent(state.catalog?.generated_at || "1");
      state.mapModule = await import(`./map-preview.js?v=${version}`);
    }
    await state.mapModule.renderMapPreview({
      container: document.querySelector("#map-preview"),
      status: elements.mapStatus,
      assets: mapAssets,
      basemap: state.basemap,
      colorField,
      selectedLayer,
      onLayerOptionsChange: (layers, layer) => updateLayerOptions(layerAsset, layers, layer),
      onColorFieldsChange: (fields) => updateColorizeFields(colorizeAsset, fields),
      onColorLegendChange: renderColorLegend,
      onFeatureSelect: renderFeatureInspector,
    });
  } catch (error) {
    elements.mapStatus.textContent = `Map unavailable. Open the PMTiles URL directly. ${error.message}`;
  }
}

function selectedColorizeAsset(assets = selectedReferences()) {
  const mapAssets = assets.filter((asset) => asset?.pmtiles_url);
  if (state.selectedSlugs.length !== 1 || mapAssets.length !== 1) {
    return null;
  }
  return mapAssets[0];
}

function selectedLayerAsset(assets = selectedReferences()) {
  const mapAssets = assets.filter((asset) => asset?.pmtiles_url);
  if (state.selectedSlugs.length !== 1 || mapAssets.length !== 1) {
    return null;
  }
  return mapAssets[0];
}

function prepareLayerControl(asset) {
  if (!asset) {
    resetLayerControl();
    return "";
  }

  const key = mapReferenceKey(asset);
  const layers = state.layerOptionsByReference[key] || [];
  elements.layerControl.hidden = false;
  if (!layers.length) {
    renderLayerOptions(asset, layers, { loading: true });
  } else {
    renderLayerOptions(asset, layers);
  }
  return selectedLayerValue(asset, layers);
}

function resetLayerControl() {
  elements.layerControl.hidden = true;
  elements.layer.disabled = true;
  elements.layer.replaceChildren(new Option("All layers", ""));
  elements.layer.value = "";
}

function updateLayerOptions(asset, layers, selectedLayer = "") {
  if (!asset) return;
  const values = unique((Array.isArray(layers) ? layers : []).map((layer) => String(layer || "").trim()));
  const key = mapReferenceKey(asset);
  state.layerOptionsByReference[key] = values;
  if (selectedLayer && values.includes(selectedLayer)) {
    state.layerByReference[key] = selectedLayer;
  } else if (state.layerByReference[key] && !values.includes(state.layerByReference[key])) {
    delete state.layerByReference[key];
  }
  renderLayerOptions(asset, values);
}

function renderLayerOptions(asset, layers, options = {}) {
  if (options.loading && !layers.length) {
    elements.layerControl.hidden = false;
    elements.layer.replaceChildren(new Option("Reading layers...", ""));
    elements.layer.disabled = true;
    elements.layer.value = "";
    return;
  }
  if (layers.length <= 1) {
    resetLayerControl();
    return;
  }
  const selected = selectedLayerValue(asset, layers);
  elements.layerControl.hidden = false;
  elements.layer.replaceChildren();
  elements.layer.append(new Option("All layers", ""));
  for (const layer of layers) {
    elements.layer.append(new Option(formatLayerLabel(layer), layer));
  }
  elements.layer.disabled = false;
  elements.layer.value = selected;
}

function selectedLayerValue(asset, layers = state.layerOptionsByReference[mapReferenceKey(asset)] || []) {
  const saved = state.layerByReference[mapReferenceKey(asset)] || "";
  if (!saved) {
    return "";
  }
  if (!layers.length || layers.includes(saved)) {
    return saved;
  }
  delete state.layerByReference[mapReferenceKey(asset)];
  return "";
}

function formatLayerLabel(layer) {
  return String(layer || "").replace(/[_-]+/g, " ");
}

function prepareColorizeControl(asset) {
  if (!asset) {
    resetColorizeControl();
    return "";
  }

  elements.colorizeControl.hidden = false;
  const key = colorReferenceKey(asset);
  const fields = state.colorFieldsByReference[key] || [];
  renderColorizeOptions(asset, fields, { loading: !fields.length });
  return selectedColorField(asset, fields);
}

function resetColorizeControl() {
  elements.colorizeControl.hidden = true;
  elements.colorize.disabled = true;
  elements.colorize.replaceChildren(new Option("None", ""));
  elements.colorize.value = "";
}

function updateColorizeFields(asset, fields) {
  if (!asset) return;
  const key = colorReferenceKey(asset);
  state.colorFieldsByReference[key] = unique(Array.isArray(fields) ? fields : []);
  const previous = elements.colorize.value;
  renderColorizeOptions(asset, state.colorFieldsByReference[key]);
  if (elements.colorize.value !== previous && state.mapModule?.setColorizeField) {
    state.mapModule.setColorizeField(elements.colorize.value);
  }
}

function renderColorizeOptions(asset, fields, options = {}) {
  if (options.loading && !fields.length) {
    elements.colorize.replaceChildren(new Option("Reading fields...", ""));
    elements.colorize.disabled = true;
    elements.colorize.value = "";
    return;
  }
  const selected = selectedColorField(asset, fields);
  elements.colorize.replaceChildren();
  elements.colorize.append(new Option("None", ""));
  for (const field of fields) {
    elements.colorize.append(new Option(field, field));
  }
  elements.colorize.disabled = !fields.length;
  elements.colorize.value = selected;
}

function selectedColorField(asset, fields) {
  const key = colorReferenceKey(asset);
  const saved = state.colorFieldByReference[key] || "";
  if (!saved || !fields.length) {
    return "";
  }
  if (fields.includes(saved)) {
    return saved;
  }
  delete state.colorFieldByReference[key];
  return "";
}

function colorReferenceKey(asset) {
  return `${mapReferenceKey(asset)}|${selectedLayerValue(asset)}`;
}

function mapReferenceKey(asset) {
  return `${asset.slug}|${selectedVersionValue(asset)}`;
}

function clearFeatureInspector() {
  elements.featureInspector.hidden = true;
  elements.featureInspector.replaceChildren();
  state.mapModule?.clearFeatureInspectionIndicator?.();
}

function clearColorLegend() {
  elements.colorLegend.hidden = true;
  elements.colorLegend.replaceChildren();
}

function renderColorLegend(legend) {
  const entries = Array.isArray(legend?.entries) ? legend.entries : [];
  if (!["categorical", "numeric", "temporal"].includes(legend?.type) || !entries.length) {
    clearColorLegend();
    return;
  }

  elements.colorLegend.replaceChildren();
  const heading = document.createElement("div");
  heading.className = "color-legend-heading";
  const title = document.createElement("strong");
  title.textContent = legend.field || "Categories";
  const count = document.createElement("span");
  count.textContent = `${entries.length} unique values`;
  heading.append(title, count);

  const items = document.createElement("div");
  items.className = "color-legend-items";
  for (const entry of entries) {
    const item = document.createElement("button");
    item.className = "color-legend-item";
    item.type = "button";
    item.title = entry.value;
    item.setAttribute("aria-pressed", entry.value === legend.focusedValue ? "true" : "false");
    if (legend.focusedValue) {
      item.classList.toggle("is-focused", entry.value === legend.focusedValue);
      item.classList.toggle("is-muted", entry.value !== legend.focusedValue);
    }
    const swatch = document.createElement("span");
    swatch.className = "color-legend-swatch";
    swatch.style.background = entry.color;
    const label = document.createElement("span");
    label.textContent = entry.value;
    item.addEventListener("click", () => {
      const toggleFocus = state.mapModule?.toggleLegendFocus || state.mapModule?.toggleCategoricalFocus;
      toggleFocus?.(entry.value);
    });
    item.append(swatch, label);
    items.append(item);
  }

  elements.colorLegend.append(heading, items);
  elements.colorLegend.hidden = false;
}

function renderFeatureInspector(features) {
  const selectedFeatures = Array.isArray(features) ? features : [features].filter(Boolean);
  if (!selectedFeatures.length) {
    clearFeatureInspector();
    return;
  }

  elements.featureInspector.replaceChildren();

  const heading = document.createElement("div");
  heading.className = "feature-inspector-heading";
  const title = document.createElement("h4");
  title.textContent = selectedFeatures.length === 1 ? "Selected object" : `${selectedFeatures.length} selected objects`;
  const meta = document.createElement("span");
  meta.textContent =
    selectedFeatures.length === 1
      ? [selectedFeatures[0].sourceLayer, selectedFeatures[0].geometryType].filter(Boolean).join(" / ")
      : "Overlapping map hits";
  heading.append(title, meta);
  elements.featureInspector.append(heading);

  for (const feature of selectedFeatures) {
    appendFeatureHit(feature);
  }
  elements.featureInspector.hidden = false;
}

function appendFeatureHit(feature) {
  const entries = Object.entries(feature.properties || {});
  const hit = document.createElement("section");
  hit.className = "feature-hit";
  hit.style.setProperty("--feature-color", feature.color || "var(--accent)");

  const hitHeader = document.createElement("div");
  hitHeader.className = "feature-hit-heading";
  const title = document.createElement("strong");
  const swatch = document.createElement("span");
  swatch.className = "selection-swatch";
  swatch.style.background = feature.color || "var(--accent)";
  title.append(swatch, document.createTextNode(feature.assetTitle || "Dataset feature"));
  const meta = document.createElement("span");
  meta.textContent = [feature.sourceLayer, feature.geometryType].filter(Boolean).join(" / ");
  hitHeader.append(title, meta);
  hit.append(hitHeader);

  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "feature-inspector-empty";
    empty.textContent = "This object has no published properties.";
    hit.append(empty);
    elements.featureInspector.append(hit);
    return;
  }

  appendFeatureTable(hit, entries);
  elements.featureInspector.append(hit);
}

function appendFeatureTable(container, entries) {
  const table = document.createElement("table");
  table.className = "feature-table";
  const tbody = document.createElement("tbody");
  for (let index = 0; index < entries.length; index += 2) {
    const row = document.createElement("tr");
    appendFeaturePair(row, entries[index], { side: "first" });
    appendFeaturePair(row, entries[index + 1], { side: "second", empty: !entries[index + 1] });
    tbody.append(row);
  }
  table.append(tbody);
  container.append(table);
}

function appendFeaturePair(row, entry, options = {}) {
  const field = document.createElement("th");
  field.scope = "row";
  const cell = document.createElement("td");
  const pairClass = options.side === "second" ? "feature-pair-second" : "feature-pair-first";
  field.classList.add("feature-pair-field", pairClass);
  cell.classList.add("feature-pair-value", pairClass);
  if (options.empty) {
    field.classList.add("feature-empty");
    cell.classList.add("feature-empty");
    field.setAttribute("aria-hidden", "true");
    cell.setAttribute("aria-hidden", "true");
  } else {
    field.textContent = entry[0];
    cell.textContent = formatFeatureValue(entry[1]);
  }
  row.append(field, cell);
}

function formatFeatureValue(value) {
  if (value === null || value === undefined || value === "") {
    return "Not provided";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

async function openDocs(asset) {
  const requestSerial = ++state.docsRequestSerial;
  elements.docsViewer.hidden = false;
  document.body.classList.add("docs-open");
  elements.docsTitle.textContent = asset.title;
  elements.docsCopyMarkdown.disabled = true;
  delete elements.docsCopyMarkdown.dataset.markdown;
  renderDocsMessage(`Loading ${asset.title} docs...`);
  elements.docsBody.focus({ preventScroll: true });

  try {
    const response = await fetch(cacheBustedUrl(asset.docs_url, state.catalog?.generated_at || Date.now()), {
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`docs returned HTTP ${response.status}`);
    }
    const markdown = await response.text();
    if (requestSerial !== state.docsRequestSerial) return;
    elements.docsCopyMarkdown.dataset.markdown = markdown;
    elements.docsCopyMarkdown.disabled = false;
    renderMarkdownDocs(markdown, elements.docsBody);
  } catch (error) {
    if (requestSerial !== state.docsRequestSerial) return;
    renderDocsError(error);
  }
}

function withPmtilesCacheBust(asset) {
  return {
    ...asset,
    pmtiles_url: cacheBustedUrl(pmtilesPreviewUrl(asset), pmtilesCacheKey(asset)),
  };
}

function pmtilesPreviewUrl(asset) {
  const path = String(asset?.pmtiles_path || "");
  const match = path.match(/^gs:\/\/([^/]+)\/(.+)$/);
  if (match) {
    return `https://storage.googleapis.com/${match[1]}/${match[2]}`;
  }
  return asset?.pmtiles_url || "";
}

function pmtilesCacheKey(asset) {
  if (asset.pmtiles_sha256) {
    return asset.pmtiles_sha256;
  }
  const hash = String(asset.notes || "").match(/\bpmtiles sha256\s+([a-f0-9]{64})\b/i);
  if (hash) {
    return hash[1];
  }
  return [asset.last_updated, state.catalog?.generated_at, asset.slug].filter(Boolean).join("-");
}

function cacheBustedUrl(url, key) {
  if (!url || !key) {
    return url;
  }
  const [withoutHash, hash = ""] = String(url).split("#", 2);
  const separator = withoutHash.includes("?") ? "&" : "?";
  return `${withoutHash}${separator}v=${encodeURIComponent(String(key))}${hash ? `#${hash}` : ""}`;
}

function closeDocs() {
  state.docsRequestSerial += 1;
  elements.docsCopyMarkdown.disabled = true;
  delete elements.docsCopyMarkdown.dataset.markdown;
  elements.docsViewer.hidden = true;
  document.body.classList.remove("docs-open");
  elements.docsBody.replaceChildren();
}

function renderDocsMessage(message) {
  elements.docsBody.replaceChildren();
  const paragraph = document.createElement("p");
  paragraph.className = "docs-loading";
  paragraph.textContent = message;
  elements.docsBody.append(paragraph);
}

function renderDocsError(error) {
  elements.docsBody.replaceChildren();
  const message = document.createElement("div");
  message.className = "error-state";
  message.textContent = `Could not load docs. ${error.message}`;
  elements.docsBody.append(message);
}

function renderMarkdownDocs(markdown, container) {
  container.replaceChildren();
  const lines = stripFrontmatter(markdown).replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (line.trimStart().startsWith("<!--")) {
      index = skipHtmlComment(lines, index);
      continue;
    }

    if (line.trimStart().startsWith("```")) {
      index = appendCodeBlock(container, lines, index);
      continue;
    }
    if (isTableStart(lines, index)) {
      index = appendMarkdownTable(container, lines, index);
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 4);
      const element = document.createElement(`h${level}`);
      appendInline(element, heading[2].trim());
      container.append(element);
      index += 1;
      continue;
    }

    if (isListLine(line)) {
      index = appendList(container, lines, index);
      continue;
    }

    index = appendParagraph(container, lines, index);
  }

  if (!container.children.length) {
    renderDocsMessage("No documentation content is available yet.");
  }
}

function stripFrontmatter(markdown) {
  if (!markdown.startsWith("---\n")) {
    return markdown;
  }
  const end = markdown.indexOf("\n---\n", 4);
  return end === -1 ? markdown : markdown.slice(end + 5);
}

function appendCodeBlock(container, lines, start) {
  const firstLine = lines[start].trim();
  const language = firstLine.slice(3).trim();
  const codeLines = [];
  let index = start + 1;
  while (index < lines.length && !lines[index].trimStart().startsWith("```")) {
    codeLines.push(lines[index]);
    index += 1;
  }
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  if (language) {
    code.dataset.language = language;
  }
  code.textContent = codeLines.join("\n");
  pre.append(code);
  container.append(pre);
  return index < lines.length ? index + 1 : index;
}

function appendMarkdownTable(container, lines, start) {
  const headers = splitMarkdownRow(lines[start]);
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const header of headers) {
    const cell = document.createElement("th");
    appendInline(cell, header);
    headRow.append(cell);
  }
  thead.append(headRow);
  table.append(thead);

  const tbody = document.createElement("tbody");
  let index = start + 2;
  while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
    const row = document.createElement("tr");
    for (const value of splitMarkdownRow(lines[index])) {
      const cell = document.createElement("td");
      appendInline(cell, value);
      row.append(cell);
    }
    tbody.append(row);
    index += 1;
  }
  table.append(tbody);

  const scroller = document.createElement("div");
  scroller.className = "docs-table-wrap";
  scroller.append(table);
  container.append(scroller);
  return index;
}

function appendList(container, lines, start) {
  const ordered = /^\s*\d+\.\s+/.test(lines[start]);
  const list = document.createElement(ordered ? "ol" : "ul");
  let index = start;
  while (index < lines.length && isListLine(lines[index]) && /^\s*\d+\.\s+/.test(lines[index]) === ordered) {
    const item = document.createElement("li");
    appendInline(item, lines[index].replace(/^\s*(?:[-*]|\d+\.)\s+/, "").trim());
    list.append(item);
    index += 1;
  }
  container.append(list);
  return index;
}

function appendParagraph(container, lines, start) {
  const paragraphLines = [];
  let index = start;
  while (index < lines.length && !isMarkdownBlockStart(lines, index)) {
    paragraphLines.push(lines[index].trim());
    index += 1;
  }
  const paragraph = document.createElement("p");
  appendInline(paragraph, paragraphLines.join(" "));
  container.append(paragraph);
  return index;
}

function isMarkdownBlockStart(lines, index) {
  const line = lines[index] || "";
  return (
    !line.trim() ||
    line.trimStart().startsWith("```") ||
    line.trimStart().startsWith("<!--") ||
    /^(#{1,4})\s+/.test(line) ||
    isTableStart(lines, index) ||
    isListLine(line)
  );
}

function skipHtmlComment(lines, start) {
  let index = start;
  while (index < lines.length) {
    if (lines[index].includes("-->")) {
      return index + 1;
    }
    index += 1;
  }
  return index;
}

function isListLine(line) {
  return /^\s*(?:[-*]|\d+\.)\s+\S/.test(line);
}

function isTableStart(lines, index) {
  return Boolean(lines[index]?.includes("|") && lines[index + 1] && isTableSeparator(lines[index + 1]));
}

function isTableSeparator(line) {
  const cells = splitMarkdownRow(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function splitMarkdownRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function appendInline(parent, text) {
  const pattern = /(`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)]+)\))/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) {
      parent.append(document.createTextNode(text.slice(cursor, match.index)));
    }
    if (match[2] !== undefined) {
      const code = document.createElement("code");
      code.textContent = match[2];
      parent.append(code);
    } else if (match[3] !== undefined) {
      const strong = document.createElement("strong");
      strong.textContent = match[3];
      parent.append(strong);
    } else if (match[4] !== undefined && match[5] !== undefined) {
      const link = document.createElement("a");
      link.textContent = match[4];
      const href = safeMarkdownHref(match[5]);
      if (href) {
        link.href = href;
        link.target = "_blank";
        link.rel = "noopener";
      }
      parent.append(link);
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    parent.append(document.createTextNode(text.slice(cursor)));
  }
}

function safeMarkdownHref(href) {
  const trimmed = String(href || "").trim();
  if (!trimmed || /^(?:javascript|data):/i.test(trimmed)) {
    return "";
  }
  return trimmed;
}

function clearDetail() {
  state.selectedSlug = null;
  state.selectedSlugs = [];
  elements.detail.hidden = true;
  elements.empty.hidden = false;
  renderSelectionLegend([]);
  clearFeatureInspector();
  elements.empty.querySelector("h2").textContent = "No matching datasets";
  elements.empty.querySelector("p:last-child").textContent =
    "Adjust search or filters to bring assets back into view.";
}

async function copyValue(value, button) {
  const text = String(value || "").trim();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    document.execCommand("copy");
    field.remove();
  }
  const original = button.textContent;
  button.textContent = "Copied";
  button.classList.add("copied");
  window.setTimeout(() => {
    button.textContent = original;
    button.classList.remove("copied");
  }, 1400);
}

function renderFatalError(error) {
  elements.count.textContent = "Catalog unavailable";
  elements.list.replaceChildren();
  const message = document.createElement("div");
  message.className = "error-state";
  message.textContent = `Could not load catalog.json. ${error.message}`;
  elements.list.append(message);
}

init();
