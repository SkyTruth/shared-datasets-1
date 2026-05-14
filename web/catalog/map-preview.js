const MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.js";
const MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.css";
const PMTILES_JS = "https://unpkg.com/pmtiles@4.3.0/dist/pmtiles.js";
const MISSING_COLOR = "#949d97";
const SAMPLE_LIMIT = 700;
const CATEGORICAL_MATCH_LIMIT = 320;
const CATEGORICAL_NUMERIC_VALUE_LIMIT = 12;
const GRADIENT_LEGEND_VALUE_LIMIT = 20;
const MIN_GRADIENT_VALUES = 4;
const GRADIENT_PARSE_RATIO = 0.86;
const IDENTIFIER_FIELD_PATTERN =
  /(^|[_\-\s])(?:id|gid|uid|uuid|key|code|metadata_i|objectid|featureid)(?:s)?($|[_\-\s])|[_\-\s]id$/i;
const STRICT_NUMBER_PATTERN = /^[+-]?(?:(?:\d+\.?\d*)|(?:\.\d+))(?:e[+-]?\d+)?$/i;
const TIME_ONLY_PATTERN = /^(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(am|pm)?$/i;
const ISO_DATE_PATTERN =
  /^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/i;
const SLASH_DATE_PATTERN = /^\d{1,4}\/\d{1,2}\/\d{1,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?)?$/i;
const MONTH_DATE_PATTERN =
  /^(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?)?$/i;
const BASEMAPS = {
  map: {
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    attribution: "© OpenStreetMap contributors",
    maxzoom: 19,
  },
  satellite: {
    tiles: ["https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
    attribution:
      "Tiles © Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    maxzoom: 19,
  },
};
const CATEGORICAL_COLORS = [
  "#d84f2a",
  "#1f6fb2",
  "#8b4ec6",
  "#b88700",
  "#16715d",
  "#9a4261",
  "#3d7b2f",
  "#d15f96",
  "#5368d7",
  "#a56722",
  "#008c95",
  "#6e6e1f",
];
const NUMERIC_RAMPS = {
  map: ["#2166ac", "#f7f7f7", "#b2182b"],
  satellite: ["#00d6ff", "#fff36a", "#ff4b7b"],
};

let dependencyPromise = null;
let pmtilesProtocol = null;
let activeMap = null;
let activeRenderSerial = 0;
let activeColorContext = null;
let activeFeatureMarker = null;
let privateSessionPromise = null;
let privateSessionUrl = "";
let privateSignerUnavailable = false;

export async function renderMapPreview({
  container,
  status,
  asset,
  assets,
  basemap = "map",
  colorField = "",
  selectedLayer = "",
  onLayerOptionsChange = () => {},
  onColorFieldsChange = () => {},
  onColorLegendChange = () => {},
  onFeatureSelect = () => {},
}) {
  const requestedAssets = (Array.isArray(assets) && assets.length ? assets : [asset]).filter(Boolean);
  const mapAssets = requestedAssets.filter((candidate) => candidate.pmtiles_url);
  if (!mapAssets.length) {
    throw new Error("No selected assets publish PMTiles.");
  }

  const renderSerial = ++activeRenderSerial;
  clearActiveMap();

  container.replaceChildren(status);
  status.hidden = false;
  status.textContent = "Loading map libraries...";

  await loadDependencies();
  if (!renderIsCurrent(renderSerial)) return;
  const protocol = installProtocol();

  const resolvedBasemap = BASEMAPS[basemap] ? basemap : "map";
  const singleDataset = mapAssets.length === 1;
  if (mapAssets.some(pmtilesCanUseSigner)) {
    status.textContent = "Authorizing private PMTiles...";
    for (let index = 0; index < mapAssets.length; index += 1) {
      mapAssets[index] = await resolvePmtilesAccess(mapAssets[index]);
      if (!renderIsCurrent(renderSerial)) return;
    }
  }
  if (mapAssets.some(pmtilesNeedsCredentials)) {
    status.textContent = "Authorizing private PMTiles...";
    await ensurePrivatePmtilesSession();
    if (!renderIsCurrent(renderSerial)) return;
  }
  status.textContent = "Reading PMTiles metadata...";
  const mapSources = [];
  for (let index = 0; index < mapAssets.length; index += 1) {
    status.textContent =
      mapAssets.length === 1
        ? "Reading PMTiles metadata..."
        : `Reading PMTiles metadata ${index + 1} of ${mapAssets.length}...`;
    mapSources.push(
      await mapSourceForAsset(mapAssets[index], index, resolvedBasemap, singleDataset ? selectedLayer : "", protocol)
    );
    if (!renderIsCurrent(renderSerial)) return;
  }
  if (!renderIsCurrent(renderSerial)) return;
  if (singleDataset) {
    const source = mapSources[0];
    onLayerOptionsChange(
      source.allSourceLayers.map((layer) => layer.sourceLayer),
      source.selectedLayer
    );
  } else {
    onLayerOptionsChange([], "");
  }

  const mapElement = document.createElement("div");
  mapElement.className = "map-canvas";
  mapElement.style.width = "100%";
  mapElement.style.height = "100%";
  container.append(mapElement);

  const map = new window.maplibregl.Map({
    container: mapElement,
    style: styleFor(mapSources, resolvedBasemap),
    attributionControl: false,
    cooperativeGestures: true,
    center: [0, 15],
    zoom: 1,
  });
  activeMap = map;
  map.addControl(new window.maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
  map.addControl(new window.maplibregl.AttributionControl({ compact: true }), "bottom-right");

  await withTimeout(
    new Promise((resolve, reject) => {
      map.once("load", resolve);
      map.once("error", (event) => reject(event.error || new Error("MapLibre reported a map error.")));
    }),
    10000,
    "Map render timed out."
  );
  if (!renderIsCurrent(renderSerial)) {
    if (activeMap === map) {
      activeMap = null;
    }
    map.remove();
    return;
  }

  const bounds = combinedBounds(mapSources.map((source) => source.bounds).filter(Boolean));
  if (bounds) {
    map.fitBounds(bounds, { padding: 34, duration: 0, maxZoom: 8 });
  }

  activeColorContext = {
    map,
    mapSources,
    basemap: resolvedBasemap,
    colorField: "",
    colorMode: { type: "dataset" },
    focusedLegendValue: "",
    fieldSignature: "",
    colorSignature: "",
    refreshTimer: null,
    onColorFieldsChange: singleDataset ? onColorFieldsChange : () => {},
    onColorLegendChange: singleDataset ? onColorLegendChange : () => {},
  };
  refreshAvailableFields(activeColorContext);
  setColorizeField(singleDataset ? colorField : "");
  if (singleDataset) {
    enableColorRefresh(activeColorContext);
  } else {
    onColorFieldsChange([]);
    onColorLegendChange(null);
  }

  enableFeatureInspection(map, mapSources, onFeatureSelect);
  status.hidden = true;
}

export function setColorizeField(fieldName) {
  const context = activeColorContext;
  if (!context || !context.map || context.mapSources.length !== 1) {
    return;
  }

  const nextField = String(fieldName || "");
  const previousField = context.colorField;
  context.colorField = context.availableFields?.includes(nextField) ? nextField : "";
  if (context.colorField !== previousField) {
    context.focusedLegendValue = "";
  }
  if (!context.colorField) {
    context.colorMode = { type: "dataset" };
    context.colorSignature = "";
    context.focusedLegendValue = "";
    applyDatasetColors(context);
    applyFocusFilters(context);
    notifyColorLegend(context);
    return;
  }

  refreshColorSample(context);
}

export function toggleCategoricalFocus(value) {
  toggleLegendFocus(value);
}

export function toggleLegendFocus(value) {
  const context = activeColorContext;
  if (!context || context.mapSources.length !== 1 || !modeSupportsLegendFocus(context.colorMode)) {
    return;
  }

  const nextValue = normalizedValue(value);
  if (!nextValue || !legendValuesForMode(context.colorMode).includes(nextValue)) {
    return;
  }

  context.focusedLegendValue = context.focusedLegendValue === nextValue ? "" : nextValue;
  applyFocusFilters(context);
  notifyColorLegend(context);
}

export function clearFeatureInspectionIndicator() {
  if (activeFeatureMarker) {
    activeFeatureMarker.remove();
    activeFeatureMarker = null;
  }
}

function clearActiveMap() {
  clearFeatureInspectionIndicator();
  clearActiveColorContext();
  if (activeMap) {
    activeMap.remove();
    activeMap = null;
  }
}

function clearActiveColorContext() {
  if (activeColorContext?.refreshTimer) {
    window.clearTimeout(activeColorContext.refreshTimer);
  }
  activeColorContext = null;
}

function renderIsCurrent(renderSerial) {
  return renderSerial === activeRenderSerial;
}

function withTimeout(promise, timeoutMs, message) {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error(message)), timeoutMs);
    promise.then(
      (value) => {
        window.clearTimeout(timer);
        resolve(value);
      },
      (error) => {
        window.clearTimeout(timer);
        reject(error);
      }
    );
  });
}

function loadDependencies() {
  if (!dependencyPromise) {
    dependencyPromise = Promise.all([
      loadCss(MAPLIBRE_CSS),
      loadScript(MAPLIBRE_JS, "maplibregl"),
      loadScript(PMTILES_JS, "pmtiles"),
    ]);
  }
  return dependencyPromise;
}

function loadCss(href) {
  if (document.querySelector(`link[href="${href}"]`)) {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    link.onload = resolve;
    link.onerror = () => reject(new Error(`Could not load ${href}`));
    document.head.append(link);
  });
}

function loadScript(src, globalName) {
  if (window[globalName]) {
    return Promise.resolve();
  }
  if (document.querySelector(`script[src="${src}"]`)) {
    return waitForGlobal(globalName);
  }
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => {
      if (window[globalName]) {
        resolve();
      } else {
        reject(new Error(`${globalName} did not initialize.`));
      }
    };
    script.onerror = () => reject(new Error(`Could not load ${src}`));
    document.head.append(script);
  });
}

function waitForGlobal(globalName) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      if (window[globalName]) {
        window.clearInterval(timer);
        resolve();
      } else if (attempts > 80) {
        window.clearInterval(timer);
        reject(new Error(`${globalName} did not initialize.`));
      }
    }, 50);
  });
}

function installProtocol() {
  if (pmtilesProtocol) {
    return pmtilesProtocol;
  }
  const protocol = new window.pmtiles.Protocol();
  window.maplibregl.addProtocol("pmtiles", protocol.tile);
  pmtilesProtocol = protocol;
  return pmtilesProtocol;
}

async function mapSourceForAsset(asset, index, basemap, selectedLayer = "", protocol) {
  const archive = pmtilesArchiveForAsset(asset);
  protocol.add(archive);
  const metadata = await withTimeout(archive.getMetadata(), 8000, `${asset.title} PMTiles metadata request timed out.`);
  const header = await withTimeout(archive.getHeader(), 8000, `${asset.title} PMTiles header request timed out.`);
  const color = palette(index, basemap);
  const allSourceLayers = vectorLayerSpecs(metadata, asset).map((spec, layerIndex) => {
    const baseId = `asset-${index}-${safeId(asset.slug)}-${layerIndex}-${safeId(spec.sourceLayer)}`;
    return {
      sourceLayer: spec.sourceLayer,
      fields: spec.fields,
      fillId: `${baseId}-fill`,
      polygonOutlineId: `${baseId}-polygon-outline`,
      lineId: `${baseId}-line`,
      pointId: `${baseId}-point`,
    };
  });
  const requestedLayer = String(selectedLayer || "");
  const sourceLayers = requestedLayer
    ? allSourceLayers.filter((layer) => layer.sourceLayer === requestedLayer)
    : allSourceLayers;
  const effectiveSourceLayers = sourceLayers.length ? sourceLayers : allSourceLayers;
  return {
    asset,
    index,
    color,
    sourceId: `dataset-${index}-${safeId(asset.slug)}`,
    allSourceLayers,
    sourceLayers: effectiveSourceLayers,
    selectedLayer: requestedLayer && sourceLayers.length ? requestedLayer : "",
    bounds: boundsFromHeader(header),
  };
}

function pmtilesArchiveForAsset(asset) {
  if (!pmtilesNeedsCredentials(asset)) {
    return new window.pmtiles.PMTiles(asset.pmtiles_url);
  }
  if (!window.pmtiles.FetchSource) {
    throw new Error("Private PMTiles require credential-aware PMTiles fetch support.");
  }
  const source = new window.pmtiles.FetchSource(asset.pmtiles_url, new Headers(), "include");
  return new window.pmtiles.PMTiles(source);
}

function pmtilesNeedsCredentials(asset) {
  const url = String(asset?.pmtiles_url || "");
  if (!url) return false;
  try {
    return new URL(url, window.location.href).pathname.startsWith("/pmtiles/private/");
  } catch {
    return url.includes("/pmtiles/private/");
  }
}

async function resolvePmtilesAccess(asset) {
  if (!pmtilesCanUseSigner(asset) || privateSignerUnavailable) {
    return asset;
  }
  const signer = privatePmtilesSignerUrl();
  if (!signer.url) {
    return asset;
  }
  const signed = await requestSignedPmtilesUrl(asset, signer);
  if (!signed) {
    return asset;
  }
  return {
    ...asset,
    pmtiles_url: signed.pmtiles_url,
    source_pmtiles_url: asset.pmtiles_url,
    signed_pmtiles_expires_at: signed.expires_at || "",
    _pmtiles_signed_url: true,
  };
}

function pmtilesCanUseSigner(asset) {
  if (!asset?.pmtiles_url || !asset?.slug) return false;
  return String(asset.access_tier || "").toLowerCase() === "private" || pmtilesNeedsCredentials(asset);
}

async function requestSignedPmtilesUrl(asset, signer) {
  const url = new URL(signer.url, window.location.href);
  url.searchParams.set("slug", asset.slug);
  const response = await fetch(url, {
    cache: "no-store",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (response.status === 404 && !signer.configured && !responseIsJson(response)) {
    privateSignerUnavailable = true;
    return null;
  }
  if (!response.ok) {
    throw new Error(`Private PMTiles signer returned HTTP ${response.status}.`);
  }
  const payload = await response.json();
  const signedUrl = String(payload?.pmtiles_url || "");
  if (!signedUrl) {
    throw new Error("Private PMTiles signer did not return a PMTiles URL.");
  }
  return {
    pmtiles_url: signedUrl,
    expires_at: String(payload?.expires_at || ""),
  };
}

function privatePmtilesSignerUrl() {
  const configured = window.SHARED_DATASETS_PMTILES_SIGNER_URL;
  if (typeof configured === "string" && configured.trim()) {
    return { url: configured.trim(), configured: true };
  }
  const meta = document.querySelector('meta[name="shared-datasets-pmtiles-signer-url"]');
  if (meta?.content?.trim()) {
    return { url: meta.content.trim(), configured: true };
  }
  if (window.location.protocol !== "http:" && window.location.protocol !== "https:") {
    return { url: "", configured: false };
  }
  if (isStorageGoogleapisHost(window.location.hostname)) {
    return { url: "", configured: false };
  }
  return { url: "/api/pmtiles/signed-url", configured: false };
}

function isStorageGoogleapisHost(hostname) {
  const host = String(hostname || "").toLowerCase();
  return host === "storage.googleapis.com" || host.endsWith(".storage.googleapis.com");
}

function responseIsJson(response) {
  return String(response.headers.get("content-type") || "").toLowerCase().includes("application/json");
}

async function ensurePrivatePmtilesSession() {
  const sessionUrl = privatePmtilesSessionUrl();
  if (!sessionUrl) return;
  if (privateSessionPromise && privateSessionUrl === sessionUrl) {
    return privateSessionPromise;
  }
  privateSessionUrl = sessionUrl;
  privateSessionPromise = fetch(sessionUrl, {
    cache: "no-store",
    credentials: "include",
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error(`Private PMTiles session returned HTTP ${response.status}.`);
      }
    })
    .catch((error) => {
      privateSessionPromise = null;
      throw error;
    });
  return privateSessionPromise;
}

function privatePmtilesSessionUrl() {
  const configured = window.SHARED_DATASETS_PMTILES_SESSION_URL;
  if (typeof configured === "string" && configured.trim()) {
    return configured.trim();
  }
  const meta = document.querySelector('meta[name="shared-datasets-pmtiles-session-url"]');
  return meta?.content?.trim() || "";
}

function vectorLayerSpecs(metadata, asset) {
  const layers = Array.isArray(metadata?.vector_layers) ? metadata.vector_layers : [];
  if (layers.length) {
    return layers
      .map((layer) => ({
        sourceLayer: String(layer?.id || layer?.name || ""),
        fields: fieldNamesFromMetadata(layer?.fields),
      }))
      .filter((layer) => layer.sourceLayer);
  }
  return [{ sourceLayer: asset.slug.replaceAll("-", "_"), fields: [] }];
}

function fieldNamesFromMetadata(fields) {
  if (Array.isArray(fields)) {
    return uniqueStrings(
      fields
        .map((field) => {
          if (typeof field === "string") return field;
          return field?.name || field?.id || "";
        })
        .filter(Boolean)
    );
  }
  if (fields && typeof fields === "object") {
    return uniqueStrings(Object.keys(fields));
  }
  return [];
}

function styleFor(mapSources, basemap) {
  const basemapConfig = BASEMAPS[basemap] || BASEMAPS.map;
  const sources = {
    basemap: {
      type: "raster",
      tiles: basemapConfig.tiles,
      tileSize: 256,
      maxzoom: basemapConfig.maxzoom,
      attribution: basemapConfig.attribution,
    },
  };
  const layers = [
    {
      id: "background",
      type: "background",
      paint: {
        "background-color": basemap === "satellite" ? "#17211b" : "#dfe7e2",
      },
    },
    {
      id: "basemap",
      type: "raster",
      source: "basemap",
      paint: {
        "raster-opacity": basemap === "satellite" ? 0.92 : 0.74,
        "raster-saturation": basemap === "satellite" ? -0.12 : -0.25,
      },
    },
  ];
  for (const source of mapSources) {
    sources[source.sourceId] = {
      type: "vector",
      url: `pmtiles://${source.asset.pmtiles_url}`,
    };
    for (const layer of source.sourceLayers) {
      layers.push({
        id: layer.fillId,
        type: "fill",
        source: source.sourceId,
        "source-layer": layer.sourceLayer,
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: {
          "fill-color": source.color,
          "fill-opacity": 0.28,
        },
      });
      layers.push({
        id: layer.polygonOutlineId,
        type: "line",
        source: source.sourceId,
        "source-layer": layer.sourceLayer,
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: {
          "line-color": source.color,
          "line-width": ["interpolate", ["linear"], ["zoom"], 0, 0.8, 6, 1.7, 10, 2.5],
          "line-opacity": 0.92,
        },
      });
      layers.push({
        id: layer.lineId,
        type: "line",
        source: source.sourceId,
        "source-layer": layer.sourceLayer,
        filter: ["==", ["geometry-type"], "LineString"],
        paint: {
          "line-color": source.color,
          "line-width": ["interpolate", ["linear"], ["zoom"], 0, 1.7, 5, 3, 9, 4.5],
          "line-opacity": 0.96,
        },
      });
      layers.push({
        id: layer.pointId,
        type: "circle",
        source: source.sourceId,
        "source-layer": layer.sourceLayer,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-color": source.color,
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 0, 3.2, 4, 4.8, 8, 7.4],
          "circle-stroke-color": basemap === "satellite" ? "#07120d" : "#ffffff",
          "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 0, 1.2, 8, 2],
          "circle-opacity": 0.92,
          "circle-stroke-opacity": 0.96,
        },
      });
    }
  }

  return {
    version: 8,
    name: "Shared datasets preview",
    sources,
    layers,
  };
}

function enableColorRefresh(context) {
  const refresh = () => scheduleColorRefresh(context);
  context.map.on("idle", refresh);
  context.map.on("moveend", refresh);
  context.map.on("zoomend", refresh);
}

function scheduleColorRefresh(context) {
  if (activeColorContext !== context) return;
  if (context.refreshTimer) {
    window.clearTimeout(context.refreshTimer);
  }
  context.refreshTimer = window.setTimeout(() => {
    context.refreshTimer = null;
    if (activeColorContext !== context) return;
    refreshAvailableFields(context);
    if (context.colorField) {
      refreshColorSample(context);
    }
  }, 80);
}

function refreshAvailableFields(context) {
  const fields = new Set();
  for (const source of context.mapSources) {
    for (const layer of source.sourceLayers) {
      for (const field of layer.fields) {
        fields.add(field);
      }
      for (const feature of querySourceLayerFeatures(context.map, source, layer).slice(0, SAMPLE_LIMIT)) {
        for (const field of Object.keys(feature.properties || {})) {
          fields.add(field);
        }
      }
    }
  }
  const availableFields = uniqueStrings([...fields]);
  const signature = availableFields.join("\n");
  context.availableFields = availableFields;
  if (signature !== context.fieldSignature) {
    context.fieldSignature = signature;
    context.onColorFieldsChange(availableFields);
  }
}

function refreshColorSample(context) {
  if (!context.colorField) {
    return;
  }
  const samples = sampleFieldValues(context, context.colorField);
  const mode = inferColorMode(context.colorField, samples);
  const signature = colorModeSignature(mode);
  const previousFocus = context.focusedLegendValue;
  context.colorMode = mode;
  syncFocusedLegendValue(context);
  if (signature !== context.colorSignature || previousFocus !== context.focusedLegendValue) {
    context.colorSignature = signature;
    applyColorMode(context);
    notifyColorLegend(context);
  }
}

function sampleFieldValues(context, field) {
  const samples = [];
  let seen = 0;
  const random = seededRandom(hashString(field));
  for (const source of context.mapSources) {
    for (const layer of source.sourceLayers) {
      for (const feature of querySourceLayerFeatures(context.map, source, layer)) {
        const properties = feature.properties || {};
        if (!Object.prototype.hasOwnProperty.call(properties, field) || isEmptyValue(properties[field])) {
          continue;
        }
        seen += 1;
        const item = { raw: properties[field], text: normalizedValue(properties[field]) };
        if (samples.length < SAMPLE_LIMIT) {
          samples.push(item);
        } else {
          const replacementIndex = Math.floor(random() * seen);
          if (replacementIndex < SAMPLE_LIMIT) {
            samples[replacementIndex] = item;
          }
        }
      }
    }
  }
  return samples;
}

function querySourceLayerFeatures(map, source, layer) {
  try {
    return map.querySourceFeatures(source.sourceId, { sourceLayer: layer.sourceLayer });
  } catch {
    return [];
  }
}

function inferColorMode(field, samples) {
  const nonEmpty = samples.filter((sample) => sample.text);
  const numericItems = nonEmpty
    .map((sample) => ({ sample, parsed: parseNumericValue(sample.raw) }))
    .filter((item) => item.parsed !== null);
  const temporalValues = nonEmpty
    .map((sample) => ({ sample, parsed: parseTemporalValue(sample.raw) }))
    .filter((item) => item.parsed !== null);
  const numericValues = numericItems.map((item) => item.parsed);
  const temporalNumbers = temporalValues.map((item) => item.parsed.value);
  const categoricalValues = uniqueSampleValues(nonEmpty);

  if (shouldUseCategoricalNumericMode(field, nonEmpty, numericItems, categoricalValues)) {
    return { type: "categorical", field, values: categoricalValues };
  }
  if (isGradientCandidate(nonEmpty.length, numericValues)) {
    const extent = extentFor(numericValues);
    return {
      type: "numeric",
      field,
      min: extent.min,
      max: extent.max,
      legendValues: gradientLegendValues(numericItems),
    };
  }
  if (isGradientCandidate(nonEmpty.length, temporalNumbers)) {
    const extent = extentFor(temporalNumbers);
    return {
      type: "temporal",
      field,
      min: extent.min,
      max: extent.max,
      values: uniqueSampleValues(temporalValues.map((item) => item.sample)),
      legendValues: gradientLegendValues(temporalValues),
    };
  }
  return { type: "categorical", field, values: categoricalValues };
}

function shouldUseCategoricalNumericMode(field, samples, numericItems, categoricalValues) {
  if (!samples.length || numericItems.length / samples.length < GRADIENT_PARSE_RATIO) {
    return false;
  }
  if (categoricalValues.length < 2 || categoricalValues.length > CATEGORICAL_MATCH_LIMIT) {
    return false;
  }
  // Numeric identifiers are labels, not measurements. Keep fields such as METADATA_I discrete.
  if (looksLikeIdentifierField(field)) {
    return true;
  }
  return (
    categoricalValues.length <= CATEGORICAL_NUMERIC_VALUE_LIMIT &&
    numericItems.every((item) => Number.isInteger(item.parsed))
  );
}

function looksLikeIdentifierField(field) {
  return IDENTIFIER_FIELD_PATTERN.test(String(field || "").trim());
}

function isGradientCandidate(totalCount, parsedValues) {
  if (totalCount < MIN_GRADIENT_VALUES || parsedValues.length / totalCount < GRADIENT_PARSE_RATIO) {
    return false;
  }
  return new Set(parsedValues.map((value) => String(value))).size >= 2;
}

function extentFor(values) {
  return {
    min: Math.min(...values),
    max: Math.max(...values),
  };
}

function uniqueSampleValues(samples) {
  return uniqueStrings(samples.map((sample) => sample.text)).slice(0, CATEGORICAL_MATCH_LIMIT);
}

function gradientLegendValues(items) {
  const byText = new Map();
  for (const item of items) {
    const text = item.sample.text;
    const parsed = typeof item.parsed === "number" ? item.parsed : item.parsed?.value;
    if (!text || !Number.isFinite(parsed) || byText.has(text)) {
      continue;
    }
    byText.set(text, parsed);
  }
  if (byText.size > GRADIENT_LEGEND_VALUE_LIMIT) {
    return [];
  }
  return [...byText.entries()]
    .sort((left, right) => left[1] - right[1] || left[0].localeCompare(right[0]))
    .map(([value]) => value);
}

function colorModeSignature(mode) {
  if (mode.type === "numeric") {
    return `${mode.type}|${mode.field}|${mode.min}|${mode.max}|${(mode.legendValues || []).join("\u001f")}`;
  }
  if (mode.type === "temporal") {
    return `${mode.type}|${mode.field}|${mode.min}|${mode.max}|${(mode.values || []).join("\u001f")}|${(
      mode.legendValues || []
    ).join("\u001f")}`;
  }
  return `${mode.type}|${mode.field}|${mode.min ?? ""}|${mode.max ?? ""}|${(mode.values || []).join("\u001f")}`;
}

function applyColorMode(context) {
  if (context.colorMode.type === "dataset") {
    applyDatasetColors(context);
    applyFocusFilters(context);
    return;
  }
  const colorExpression = colorExpressionForMode(context.colorMode, context.basemap);
  for (const source of context.mapSources) {
    applySourceColor(context.map, source, colorExpression);
  }
  applyFocusFilters(context);
}

function notifyColorLegend(context) {
  const mode = context.colorMode;
  const values = legendValuesForMode(mode);
  if (!mode || !values.length) {
    context.onColorLegendChange(null);
    return;
  }
  context.onColorLegendChange({
    type: mode.type,
    field: mode.field,
    focusedValue: context.focusedLegendValue || "",
    entries: values.map((value) => ({
      value,
      color: legendColorForValue(mode, value, context.basemap),
    })),
  });
}

function applyDatasetColors(context) {
  for (const source of context.mapSources) {
    applySourceColor(context.map, source, source.color);
  }
}

function applySourceColor(map, source, color) {
  for (const layer of source.sourceLayers) {
    setPaintColor(map, layer.fillId, "fill-color", color);
    setPaintColor(map, layer.polygonOutlineId, "line-color", color);
    setPaintColor(map, layer.lineId, "line-color", color);
    setPaintColor(map, layer.pointId, "circle-color", color);
  }
}

function setPaintColor(map, layerId, property, color) {
  if (map.getLayer(layerId)) {
    map.setPaintProperty(layerId, property, color);
  }
}

function syncFocusedLegendValue(context) {
  if (
    !modeSupportsLegendFocus(context.colorMode) ||
    !context.focusedLegendValue ||
    !legendValuesForMode(context.colorMode).includes(context.focusedLegendValue)
  ) {
    context.focusedLegendValue = "";
  }
}

function applyFocusFilters(context) {
  const focusedValue =
    modeSupportsLegendFocus(context.colorMode) && context.focusedLegendValue ? context.focusedLegendValue : "";
  for (const source of context.mapSources) {
    for (const layer of source.sourceLayers) {
      setLayerFilter(context.map, layer.fillId, layerFilter("Polygon", context.colorMode, focusedValue));
      setLayerFilter(context.map, layer.polygonOutlineId, layerFilter("Polygon", context.colorMode, focusedValue));
      setLayerFilter(context.map, layer.lineId, layerFilter("LineString", context.colorMode, focusedValue));
      setLayerFilter(context.map, layer.pointId, layerFilter("Point", context.colorMode, focusedValue));
    }
  }
}

function setLayerFilter(map, layerId, filter) {
  if (map.getLayer(layerId)) {
    map.setFilter(layerId, filter);
  }
}

function layerFilter(geometryType, mode, focusedValue) {
  const geometryFilter = ["==", ["geometry-type"], geometryType];
  if (!modeSupportsLegendFocus(mode) || !mode.field || !focusedValue) {
    return geometryFilter;
  }
  return ["all", geometryFilter, ["==", fieldStringExpression(mode.field), focusedValue]];
}

function modeSupportsLegendFocus(mode) {
  return Boolean(mode?.field && legendValuesForMode(mode).length);
}

function legendValuesForMode(mode) {
  if (!["categorical", "numeric", "temporal"].includes(mode?.type)) {
    return [];
  }
  if (mode.type === "numeric" || mode.type === "temporal") {
    return Array.isArray(mode.legendValues) ? mode.legendValues : [];
  }
  return Array.isArray(mode.values) ? mode.values : [];
}

function legendColorForValue(mode, value, basemap) {
  if (mode.type === "numeric") {
    const numeric = parseNumericValue(value);
    return numeric === null ? MISSING_COLOR : gradientColor(numeric, mode.min, mode.max, basemap);
  }
  if (mode.type === "temporal") {
    const temporal = parseTemporalValue(value);
    return temporal === null ? MISSING_COLOR : gradientColor(temporal.value, mode.min, mode.max, basemap);
  }
  return categoricalColor(value);
}

function colorExpressionForMode(mode, basemap) {
  if (mode.type === "numeric") {
    return numericColorExpression(mode.field, mode.min, mode.max, basemap);
  }
  if (mode.type === "temporal") {
    return temporalColorExpression(mode.field, mode.values, mode.min, mode.max, basemap);
  }
  return categoricalColorExpression(mode.field, mode.values);
}

function numericColorExpression(field, min, max, basemap) {
  const ramp = NUMERIC_RAMPS[basemap] || NUMERIC_RAMPS.map;
  const midpoint = min + (max - min) / 2;
  const sentinel = min - Math.max(1, Math.abs(max - min));
  const numericValue = ["to-number", ["get", field], sentinel];
  return [
    "case",
    ["all", presentExpression(field), ["!=", numericValue, sentinel]],
    ["interpolate", ["linear"], numericValue, min, ramp[0], midpoint, ramp[1], max, ramp[2]],
    MISSING_COLOR,
  ];
}

function temporalColorExpression(field, values, min, max, basemap) {
  const matches = [];
  for (const value of values) {
    const parsed = parseTemporalValue(value);
    if (parsed !== null) {
      matches.push(value, gradientColor(parsed.value, min, max, basemap));
    }
  }
  if (!matches.length) {
    return MISSING_COLOR;
  }
  return ["case", presentExpression(field), ["match", fieldStringExpression(field), ...matches, MISSING_COLOR], MISSING_COLOR];
}

function categoricalColorExpression(field, values) {
  const matches = values.flatMap((value) => [value, categoricalColor(value)]);
  if (!matches.length) {
    return MISSING_COLOR;
  }
  return ["case", presentExpression(field), ["match", fieldStringExpression(field), ...matches, MISSING_COLOR], MISSING_COLOR];
}

function presentExpression(field) {
  return ["all", ["has", field], ["!=", fieldStringExpression(field), ""]];
}

function fieldStringExpression(field) {
  return ["to-string", ["coalesce", ["get", field], ""]];
}

function enableFeatureInspection(map, mapSources, onFeatureSelect) {
  const inspectableLayers = mapSources.flatMap(layerIdsForSource);
  const sourcesById = new Map(mapSources.map((source) => [source.sourceId, source]));
  map.on("click", (event) => {
    const features = map.queryRenderedFeatures(event.point, { layers: inspectableLayers });
    const selectedFeatures = serializeFeatures(features, sourcesById);
    if (!selectedFeatures.length) {
      clearFeatureInspectionIndicator();
      onFeatureSelect([]);
      return;
    }
    renderFeatureInspectionIndicator(map, event.lngLat);
    onFeatureSelect(selectedFeatures);
  });
  map.on("mousemove", (event) => {
    const features = map.queryRenderedFeatures(event.point, { layers: inspectableLayers });
    map.getCanvas().style.cursor = features.length ? "pointer" : "";
  });
  map.getCanvas().addEventListener("mouseleave", () => {
    map.getCanvas().style.cursor = "";
  });
}

function renderFeatureInspectionIndicator(map, lngLat) {
  clearFeatureInspectionIndicator();
  const element = document.createElement("div");
  element.className = "map-click-target";
  element.setAttribute("aria-hidden", "true");
  activeFeatureMarker = new window.maplibregl.Marker({ element, anchor: "center" }).setLngLat(lngLat).addTo(map);
}

function layerIdsForSource(source) {
  return source.sourceLayers.flatMap((layer) => [layer.fillId, layer.polygonOutlineId, layer.lineId, layer.pointId]);
}

function serializeFeatures(features, sourcesById) {
  const serialized = [];
  const seen = new Set();
  for (const feature of features) {
    const source = sourcesById.get(feature?.source);
    if (!source) continue;
    const item = serializeFeature(feature, source);
    const key = `${item.assetSlug}|${item.sourceLayer}|${item.geometryType}|${JSON.stringify(item.properties)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    serialized.push(item);
  }
  return serialized;
}

function serializeFeature(feature, source) {
  const properties = feature.properties || {};
  return {
    assetSlug: source.asset.slug,
    assetTitle: source.asset.title,
    color: colorForFeature(properties, source),
    sourceLayer: feature.sourceLayer || feature.layer?.["source-layer"] || "",
    geometryType: feature.geometry?.type || geometryTypeFromLayer(feature.layer?.type),
    properties,
  };
}

function colorForFeature(properties, source) {
  const mode = activeColorContext?.colorMode;
  if (!mode || mode.type === "dataset" || !mode.field) {
    return source.color;
  }
  const value = properties?.[mode.field];
  if (isEmptyValue(value)) {
    return MISSING_COLOR;
  }
  if (mode.type === "numeric") {
    const numeric = parseNumericValue(value);
    return numeric === null ? MISSING_COLOR : gradientColor(numeric, mode.min, mode.max, activeColorContext.basemap);
  }
  if (mode.type === "temporal") {
    const temporal = parseTemporalValue(value);
    return temporal === null ? MISSING_COLOR : gradientColor(temporal.value, mode.min, mode.max, activeColorContext.basemap);
  }
  return categoricalColor(normalizedValue(value));
}

function geometryTypeFromLayer(layerType) {
  if (layerType === "circle") return "Point";
  if (layerType === "line") return "LineString";
  if (layerType === "fill") return "Polygon";
  return "";
}

function parseNumericValue(value) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value !== "string") {
    return null;
  }
  const text = value.trim();
  if (!STRICT_NUMBER_PATTERN.test(text)) {
    return null;
  }
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
}

function parseTemporalValue(value) {
  if (typeof value !== "string") {
    return null;
  }
  const text = value.trim();
  if (!text) {
    return null;
  }

  const timeOnly = parseTimeOnlyValue(text);
  if (timeOnly !== null) {
    return { value: timeOnly };
  }
  if (/^\d{8}$/.test(text)) {
    const year = Number(text.slice(0, 4));
    const month = Number(text.slice(4, 6));
    const day = Number(text.slice(6, 8));
    const time = Date.UTC(year, month - 1, day);
    return validDateParts(year, month, day, time) ? { value: time } : null;
  }
  if (STRICT_NUMBER_PATTERN.test(text)) {
    return null;
  }
  if (ISO_DATE_PATTERN.test(text) || SLASH_DATE_PATTERN.test(text) || MONTH_DATE_PATTERN.test(text)) {
    const time = Date.parse(text);
    return Number.isFinite(time) ? { value: time } : null;
  }
  return null;
}

function parseTimeOnlyValue(text) {
  const match = text.match(TIME_ONLY_PATTERN);
  if (!match) {
    return null;
  }
  let hour = Number(match[1]);
  const minute = Number(match[2]);
  const second = Number(match[3] || 0);
  const period = match[4]?.toLowerCase();
  if (period === "pm" && hour < 12) hour += 12;
  if (period === "am" && hour === 12) hour = 0;
  if (hour > 23 || minute > 59 || second > 59) {
    return null;
  }
  return hour * 3600 + minute * 60 + second;
}

function validDateParts(year, month, day, time) {
  if (!Number.isFinite(time)) {
    return false;
  }
  const date = new Date(time);
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function gradientColor(value, min, max, basemap) {
  if (!Number.isFinite(value) || min === max) {
    return MISSING_COLOR;
  }
  const ramp = NUMERIC_RAMPS[basemap] || NUMERIC_RAMPS.map;
  const ratio = Math.max(0, Math.min(1, (value - min) / (max - min)));
  if (ratio <= 0.5) {
    return interpolateHex(ramp[0], ramp[1], ratio * 2);
  }
  return interpolateHex(ramp[1], ramp[2], (ratio - 0.5) * 2);
}

function categoricalColor(value) {
  return CATEGORICAL_COLORS[hashString(value) % CATEGORICAL_COLORS.length];
}

function interpolateHex(start, end, amount) {
  const a = hexToRgb(start);
  const b = hexToRgb(end);
  return rgbToHex({
    r: Math.round(a.r + (b.r - a.r) * amount),
    g: Math.round(a.g + (b.g - a.g) * amount),
    b: Math.round(a.b + (b.b - a.b) * amount),
  });
}

function hexToRgb(hex) {
  const value = Number.parseInt(hex.slice(1), 16);
  return {
    r: (value >> 16) & 255,
    g: (value >> 8) & 255,
    b: value & 255,
  };
}

function rgbToHex(color) {
  return `#${[color.r, color.g, color.b].map((value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function normalizedValue(value) {
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value ?? "").trim();
}

function isEmptyValue(value) {
  return value === null || value === undefined || normalizedValue(value) === "";
}

function uniqueStrings(values) {
  return [...new Set(values.map((value) => String(value || "").trim()).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b)
  );
}

function seededRandom(seed) {
  let state = seed || 1;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function hashString(value) {
  let hash = 2166136261;
  for (const char of String(value)) {
    hash ^= char.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function palette(index, basemap) {
  const colors =
    basemap === "satellite"
      ? ["#00d6ff", "#ffd84d", "#ff7bb7", "#78ff8e", "#ff9d42", "#caa7ff"]
      : ["#d84f2a", "#1f6fb2", "#8b4ec6", "#b88700", "#16715d", "#9a4261"];
  return colors[index % colors.length];
}

function safeId(value) {
  return (
    String(value || "layer")
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "layer"
  );
}

function boundsFromHeader(header) {
  const values = [header.minLon, header.minLat, header.maxLon, header.maxLat].map(Number);
  if (!values.every(Number.isFinite)) {
    return null;
  }
  const [minLon, minLat, maxLon, maxLat] = values;
  if (minLon >= maxLon || minLat >= maxLat) {
    return null;
  }
  return [
    [minLon, minLat],
    [maxLon, maxLat],
  ];
}

function combinedBounds(boundsList) {
  if (!boundsList.length) {
    return null;
  }
  let minLon = Infinity;
  let minLat = Infinity;
  let maxLon = -Infinity;
  let maxLat = -Infinity;
  for (const bounds of boundsList) {
    minLon = Math.min(minLon, bounds[0][0]);
    minLat = Math.min(minLat, bounds[0][1]);
    maxLon = Math.max(maxLon, bounds[1][0]);
    maxLat = Math.max(maxLat, bounds[1][1]);
  }
  if (![minLon, minLat, maxLon, maxLat].every(Number.isFinite) || minLon >= maxLon || minLat >= maxLat) {
    return null;
  }
  return [
    [minLon, minLat],
    [maxLon, maxLat],
  ];
}
