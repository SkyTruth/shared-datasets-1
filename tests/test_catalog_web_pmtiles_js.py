from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CatalogWebPmtilesJavascriptTests(unittest.TestCase):
    def test_private_pmtiles_can_use_same_origin_signer_before_cdn_cookie_fallback(self):
        map_preview = (REPO_ROOT / "web/catalog/map-preview.js").read_text()
        app = (REPO_ROOT / "web/catalog/app.js").read_text()

        self.assertIn('"/api/pmtiles/signed-url"', map_preview)
        self.assertIn("requestSignedPmtilesUrl", map_preview)
        self.assertIn('credentials: "include"', map_preview)
        self.assertIn("_pmtiles_signed_url", map_preview)
        self.assertIn("pmtilesNeedsCredentials(asset)", map_preview)
        self.assertIn("new window.pmtiles.PMTiles(asset.pmtiles_url)", map_preview)
        self.assertIn("new window.pmtiles.FetchSource(asset.pmtiles_url, new Headers(), \"include\")", map_preview)
        self.assertIn("isStorageGoogleapisHost", map_preview)
        self.assertIn("Signed PMTiles access was rejected or expired", app)

    def test_numeric_identifier_fields_use_categorical_color_mode_before_gradients(self):
        map_preview = (REPO_ROOT / "web/catalog/map-preview.js").read_text()
        infer_color_mode = map_preview[map_preview.index("function inferColorMode") :]

        self.assertIn("IDENTIFIER_FIELD_PATTERN", map_preview)
        self.assertIn("metadata_i", map_preview)
        self.assertIn("shouldUseCategoricalNumericMode", map_preview)
        self.assertLess(
            infer_color_mode.index("shouldUseCategoricalNumericMode"),
            infer_color_mode.index("isGradientCandidate"),
        )

    def test_catalog_viewer_can_zoom_to_selected_map_assets(self):
        app = (REPO_ROOT / "web/catalog/app.js").read_text()
        map_preview = (REPO_ROOT / "web/catalog/map-preview.js").read_text()

        self.assertIn('button.id = "zoom-selection"', app)
        self.assertIn("Zoom to selection", app)
        self.assertIn("color-legend-actions", app)
        self.assertIn("actions.append(elements.zoomSelection)", app)
        self.assertIn("state.mapModule?.zoomToSelection", app)
        self.assertIn("legend.focusedValue && state.mapModule?.canZoomToLegendSelection?.()", app)
        self.assertIn("export function zoomToSelection()", map_preview)
        self.assertIn("export function canZoomToLegendSelection()", map_preview)
        self.assertIn("focusedLegendBounds() || activeSelectionBounds", map_preview)
        self.assertIn("mode.boundsByValue = samples.boundsByValue", map_preview)
        self.assertIn("context.colorMode?.boundsByValue?.get", map_preview)
        self.assertIn("queryRenderedFeatures(queryBox", map_preview)

    def test_catalog_viewer_has_one_click_fgb_download_control(self):
        app = (REPO_ROOT / "web/catalog/app.js").read_text()
        html = (REPO_ROOT / "web/catalog/index.html").read_text()

        self.assertIn('id="download-fgb"', html)
        self.assertIn('class="detail-actions"', html)
        self.assertLess(html.index('id="detail-docs"'), html.index('id="download-fgb"'))
        self.assertNotIn("download-fgb-row", html)
        self.assertNotIn("detail-download-gs", html)
        self.assertIn("renderFgbDownload(asset, reference)", app)
        self.assertIn("selectedReference(asset)", app)
        self.assertIn("elements.downloadFgb.hidden = false", app)
        self.assertIn("elements.downloadFgb.hidden = true", app)
        self.assertIn("/api/download-url?", app)
        self.assertIn('format: "fgb"', app)
        self.assertIn('credentials: "include"', app)
        self.assertIn("payload.download_url", app)
        self.assertIn("triggerBrowserDownload(downloadUrl", app)
        self.assertNotIn(".blob()", app)
        self.assertNotIn("createObjectURL", app)

    def test_runtime_release_index_hydration_preserves_all_release_files(self):
        app = (REPO_ROOT / "web/catalog/app.js").read_text()

        self.assertIn("files: releaseFiles(files)", app)
        self.assertIn("function releaseFiles(files)", app)
        self.assertIn(".filter((file) => releaseFilePath(file))", app)
        self.assertIn(".map((file) => ({ ...file, path: releaseFilePath(file) }))", app)

    def test_catalog_viewer_populates_clicked_features_from_metadata_sidecar_lookup(self):
        app = (REPO_ROOT / "web/catalog/app.js").read_text()
        map_preview = (REPO_ROOT / "web/catalog/map-preview.js").read_text()

        self.assertIn("featureLookupSerial", app)
        self.assertIn("onFeatureSelect: handleFeatureSelect", app)
        self.assertIn("featureMetadataCache", app)
        self.assertIn("featureMetadataRequests", app)
        self.assertIn("warmFeatureMetadataCaches(rawMapAssets)", app)
        self.assertIn("activeMetadataLocale", app)
        self.assertIn("normalizeMetadataLocale", app)
        self.assertIn("metadataLanguage", app)
        self.assertIn("availableMetadataLocales", app)
        self.assertIn("metadataSidecarFileForReference", app)
        self.assertIn("refreshFeatureInspectorMetadata", app)
        self.assertIn("function featureMetadataDownloadUrl", app)
        self.assertIn('format: "metadata"', app)
        self.assertIn('params.set("locale", normalizedLocale)', app)
        self.assertIn('releaseFilePath(file).endsWith(".metadata.ndjson.gz")', app)
        self.assertIn('path.endsWith(`.metadata.${normalizedLocale}.ndjson.gz`)', app)
        self.assertIn("renderMetadataSidecarPath", app)
        self.assertIn("parseFeatureMetadataSidecar", app)
        self.assertIn("DecompressionStream", app)
        self.assertIn('credentials: "include"', app)
        self.assertNotIn(":lookup", app)
        self.assertNotIn("translation overlay", app.lower())
        self.assertIn("item.properties", app)
        self.assertIn("feature_id: featureId", app)
        self.assertIn("ext_id: item.ext_id", app)
        self.assertIn("source.asset.date || source.asset.latest_release?.date || source.asset.last_updated || \"latest\"", map_preview)

    def test_catalog_viewer_has_metadata_language_selector_and_sidecar_path(self):
        app = (REPO_ROOT / "web/catalog/app.js").read_text()
        html = (REPO_ROOT / "web/catalog/index.html").read_text()
        styles = (REPO_ROOT / "web/catalog/styles.css").read_text()

        self.assertIn('id="metadata-language-control"', html)
        self.assertIn('id="metadata-language-select"', html)
        self.assertIn('id="metadata-path-row"', html)
        self.assertIn('id="detail-metadata"', html)
        self.assertIn('id="copy-metadata"', html)
        self.assertIn("metadataLanguage.addEventListener(\"change\"", app)
        self.assertIn("renderMetadataSidecarPath(selectedMetadataLanguageAsset())", app)
        self.assertIn("warmFeatureMetadataCaches(selectedMapReferences())", app)
        self.assertIn("refreshFeatureInspectorMetadata()", app)
        self.assertIn(".metadata-language-control", styles)


if __name__ == "__main__":
    unittest.main()
