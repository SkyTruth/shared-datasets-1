const MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.js";
const MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@5.9.0/dist/maplibre-gl.css";
const PMTILES_JS = "https://unpkg.com/pmtiles@4.3.0/dist/pmtiles.js";
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

let dependencyPromise = null;
let protocolInstalled = false;
let activeMap = null;
let activeRenderSerial = 0;

export async function renderMapPreview({ container, status, asset, assets, basemap = "map", onFeatureSelect = () => {} }) {
  const requestedAssets = (Array.isArray(assets) && assets.length ? assets : [asset]).filter(Boolean);
  const mapAssets = requestedAssets.filter((candidate) => candidate.pmtiles_url);
  if (!mapAssets.length) {
    throw new Error("No selected assets publish PMTiles.");
  }

  const renderSerial = ++activeRenderSerial;
  if (activeMap) {
    activeMap.remove();
    activeMap = null;
  }

  container.replaceChildren(status);
  status.hidden = false;
  status.textContent = "Loading map libraries...";

  await loadDependencies();
  if (!renderIsCurrent(renderSerial)) return;
  installProtocol();

  const resolvedBasemap = BASEMAPS[basemap] ? basemap : "map";
  status.textContent = "Reading PMTiles metadata...";
  const mapSources = [];
  for (let index = 0; index < mapAssets.length; index += 1) {
    status.textContent =
      mapAssets.length === 1
        ? "Reading PMTiles metadata..."
        : `Reading PMTiles metadata ${index + 1} of ${mapAssets.length}...`;
    mapSources.push(await mapSourceForAsset(mapAssets[index], index, resolvedBasemap));
    if (!renderIsCurrent(renderSerial)) return;
  }
  if (!renderIsCurrent(renderSerial)) return;

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
    map.remove();
    return;
  }

  const bounds = combinedBounds(mapSources.map((source) => source.bounds).filter(Boolean));
  if (bounds) {
    map.fitBounds(bounds, { padding: 34, duration: 0, maxZoom: 8 });
  }
  enableFeatureInspection(map, mapSources, onFeatureSelect);
  status.hidden = true;
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
  if (protocolInstalled) {
    return;
  }
  const protocol = new window.pmtiles.Protocol();
  window.maplibregl.addProtocol("pmtiles", protocol.tile);
  protocolInstalled = true;
}

async function mapSourceForAsset(asset, index, basemap) {
  const archive = new window.pmtiles.PMTiles(asset.pmtiles_url);
  const metadata = await withTimeout(archive.getMetadata(), 8000, `${asset.title} PMTiles metadata request timed out.`);
  const header = await withTimeout(archive.getHeader(), 8000, `${asset.title} PMTiles header request timed out.`);
  const color = palette(index, basemap);
  const sourceLayers = vectorLayers(metadata, asset).map((sourceLayer, layerIndex) => {
    const baseId = `asset-${index}-${safeId(asset.slug)}-${layerIndex}-${safeId(sourceLayer)}`;
    return {
      sourceLayer,
      fillId: `${baseId}-fill`,
      polygonOutlineId: `${baseId}-polygon-outline`,
      lineId: `${baseId}-line`,
      pointId: `${baseId}-point`,
    };
  });
  return {
    asset,
    index,
    color,
    sourceId: `dataset-${index}-${safeId(asset.slug)}`,
    sourceLayers,
    bounds: boundsFromHeader(header),
  };
}

function vectorLayers(metadata, asset) {
  const layers = Array.isArray(metadata?.vector_layers) ? metadata.vector_layers : [];
  if (layers.length) {
    return layers.map((layer) => String(layer.id || layer.name)).filter(Boolean);
  }
  return [asset.slug.replaceAll("-", "_")];
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

function enableFeatureInspection(map, mapSources, onFeatureSelect) {
  const inspectableLayers = mapSources.flatMap(layerIdsForSource);
  const sourcesById = new Map(mapSources.map((source) => [source.sourceId, source]));
  map.on("click", (event) => {
    const features = map.queryRenderedFeatures(event.point, { layers: inspectableLayers });
    onFeatureSelect(serializeFeatures(features, sourcesById));
  });
  map.on("mousemove", (event) => {
    const features = map.queryRenderedFeatures(event.point, { layers: inspectableLayers });
    map.getCanvas().style.cursor = features.length ? "pointer" : "";
  });
  map.getCanvas().addEventListener("mouseleave", () => {
    map.getCanvas().style.cursor = "";
  });
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
  return {
    assetSlug: source.asset.slug,
    assetTitle: source.asset.title,
    color: source.color,
    sourceLayer: feature.sourceLayer || feature.layer?.["source-layer"] || "",
    geometryType: feature.geometry?.type || geometryTypeFromLayer(feature.layer?.type),
    properties: feature.properties || {},
  };
}

function geometryTypeFromLayer(layerType) {
  if (layerType === "circle") return "Point";
  if (layerType === "line") return "LineString";
  if (layerType === "fill") return "Polygon";
  return "";
}

function palette(index, basemap) {
  const colors =
    basemap === "satellite"
      ? ["#00d6ff", "#ffd84d", "#ff7bb7", "#78ff8e", "#ff9d42", "#caa7ff"]
      : ["#d84f2a", "#1f6fb2", "#8b4ec6", "#b88700", "#16715d", "#9a4261"];
  return colors[index % colors.length];
}

function safeId(value) {
  return String(value || "layer")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "") || "layer";
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
