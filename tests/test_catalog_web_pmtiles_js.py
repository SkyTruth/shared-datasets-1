from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def assert_contains_all(testcase: unittest.TestCase, text: str, markers: tuple[str, ...]) -> None:
    for marker in markers:
        with testcase.subTest(marker=marker):
            testcase.assertIn(marker, text)


class CatalogWebPmtilesJavascriptTests(unittest.TestCase):
    def test_catalog_viewer_javascript_is_syntactically_valid(self):
        for path in (REPO_ROOT / "web/catalog/app.js", REPO_ROOT / "web/catalog/map-preview.js"):
            with self.subTest(path=path.name):
                result = subprocess.run(
                    ["node", "--check", str(path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_static_shell_exposes_map_download_and_metadata_controls(self):
        app = source("web/catalog/app.js")
        html = source("web/catalog/index.html")
        styles = source("web/catalog/styles.css")

        assert_contains_all(
            self,
            html,
            (
                'id="download-fgb"',
                'id="detail-slug"',
                'id="metadata-language-control"',
                'id="metadata-language-select"',
                'id="metadata-path-row"',
                'id="detail-metadata"',
                'id="copy-metadata"',
            ),
        )
        self.assertLess(html.index('id="detail-docs"'), html.index('id="download-fgb"'))
        assert_contains_all(
            self,
            app,
            (
                "renderDocsLink(asset)",
                "elements.slug.textContent = asset.slug",
                "elements.docs.hidden = !docsUrl",
                "elements.docs.removeAttribute(\"href\")",
                "if (asset && asset.docs_url)",
                "metadataLanguage.addEventListener(\"change\"",
                "renderFgbDownload(asset, reference)",
                "renderMetadataSidecarPath(selectedMetadataLanguageAsset())",
                "refreshFeatureInspectorMetadata()",
            ),
        )
        self.assertNotIn("warmFeatureMetadataCaches", app)
        self.assertIn(".detail-slug", styles)
        self.assertIn(".metadata-language-control", styles)

    def test_private_pmtiles_and_fgb_downloads_use_server_authorized_urls(self):
        app = source("web/catalog/app.js")
        map_preview = source("web/catalog/map-preview.js")

        assert_contains_all(
            self,
            map_preview,
            (
                '"/api/pmtiles/signed-url"',
                "requestSignedPmtilesUrl",
                "_pmtiles_signed_url",
                "pmtilesNeedsCredentials(asset)",
                '"/pmtiles/internal/"',
                "restrictedPmtilesTiers(mapAssets)",
                "ensureRestrictedPmtilesSessions(credentialTiers)",
                "new window.pmtiles.PMTiles(asset.pmtiles_url)",
                "new window.pmtiles.FetchSource(asset.pmtiles_url, new Headers(), \"include\")",
                "isStorageGoogleapisHost",
            ),
        )
        assert_contains_all(
            self,
            app,
            (
                "Signed PMTiles access was rejected or expired",
                "/api/download-url?",
                'format: "fgb"',
                'credentials: "include"',
                "payload.download_url",
                "triggerBrowserDownload(downloadUrl",
            ),
        )
        self.assertNotIn(".blob()", app)
        self.assertNotIn("createObjectURL", app)

    def test_map_selection_and_identifier_color_mode_contracts_are_present(self):
        app = source("web/catalog/app.js")
        map_preview = source("web/catalog/map-preview.js")
        infer_color_mode = map_preview[map_preview.index("function inferColorMode") :]

        self.assertIn("IDENTIFIER_FIELD_PATTERN", map_preview)
        self.assertLess(
            infer_color_mode.index("shouldUseCategoricalNumericMode"),
            infer_color_mode.index("isGradientCandidate"),
        )
        # Integer measures such as fatalities keep numeric gradients: only
        # identifier-like field names may force categorical-numeric mode.
        self.assertIn("return looksLikeIdentifierField(field);", map_preview)
        self.assertNotIn("CATEGORICAL_NUMERIC_VALUE_LIMIT", map_preview)
        assert_contains_all(
            self,
            app,
            (
                'button.id = "zoom-selection"',
                "state.mapModule?.zoomToSelection",
                "legend.focusedValue && state.mapModule?.canZoomToLegendSelection?.()",
            ),
        )
        assert_contains_all(
            self,
            map_preview,
            (
                "export function zoomToSelection()",
                "export function canZoomToLegendSelection()",
                "focusedLegendBounds() || activeSelectionBounds",
                "queryRenderedFeatures(queryBox",
            ),
        )

    def test_unavailable_color_field_reason_is_surfaced_in_ui(self):
        app = source("web/catalog/app.js")
        map_preview = source("web/catalog/map-preview.js")

        # map-preview reports why a metadata color field cannot load, and the
        # app must carry that reason into a visible note instead of silently
        # resetting the colorize dropdown to None.
        self.assertIn("context.onColorFieldUnavailable(field, result.unavailableReason || \"\")", map_preview)
        assert_contains_all(
            self,
            app,
            (
                "onColorFieldUnavailable: (field, reason) => clearUnavailableColorField(colorizeAsset, field, reason)",
                "function clearUnavailableColorField(asset, field, reason = \"\")",
                "renderColorizeUnavailableNote(fieldName, reason)",
                'note.className = "color-legend-note"',
                "is unavailable: ${detail}",
            ),
        )
        self.assertIn(".color-legend-note", source("web/catalog/styles.css"))

    def test_release_metadata_sidecars_drive_inspector_and_colorization(self):
        # Runtime release/cache/signing behavior is exercised in
        # test_catalog_release_reference_js.py. Keep only UI wiring smoke checks.
        app = source("web/catalog/app.js")
        map_preview = source("web/catalog/map-preview.js")
        assert_contains_all(self, app, (
            "loadFeatureMetadataColorValues", "onFeatureSelect: handleFeatureSelect",
            "lookupMatchesReference(payload, group.reference)", "featureMetadataSchemaCache",
            "parseFeatureMetadataSidecarLookup", "release-reference.js",
        ))
        assert_contains_all(
            self,
            map_preview,
            (
                "accessTier: source.asset.access_tier || \"\"",
                "loadFeatureMetadataColorValues = null",
                "metadataColorValueSource",
                "colorFieldValueSource(context, context.colorField) === \"metadata\"",
                "applyMetadataFeatureState",
                "map.setFeatureState",
                "promoteId: FEATURE_ID_PROPERTY",
                "metadataColorExpressionForMode",
                "featureIdForProperties",
                "result?.unavailable",
                "resetMetadataColorMode(context, { clearField: true })",
                "context.onColorFieldUnavailable(field, result.unavailableReason || \"\")",
                "function resetMetadataColorMode",
                "context.colorField = \"\"",
                "asset?.colorizer_metadata?.source",
                'source === "metadata_sidecar_schema"',
                'source === "pmtiles_vector_layers"',
                'source === "none"',
                'fields: colorizerSource === "pmtiles_vector_layers" ? fieldNamesFromMetadata(layer?.fields) : []',
            ),
        )
        self.assertNotIn("querySourceLayerFeatures(context.map, source, layer).slice", map_preview)
        self.assertNotIn("featureMetadataColorFields", app)
        lookup_start = app.index("async function lookupFeatureMetadata(group)")
        index_start = app.index("async function featureMetadataIndex")
        lookup_slice = app[lookup_start:index_start]
        self.assertLess(
            lookup_slice.index("catalogViewerShouldAutoloadFeatureMetadata"),
            lookup_slice.index("lookupFeatureMetadataViaApi"),
        )
        self.assertLess(lookup_slice.index("lookupFeatureMetadataViaApi"), lookup_slice.index("lookupFeatureMetadataFromPublicSidecar"))
        self.assertLess(lookup_slice.index("featureMetadataIndex"), lookup_slice.index("lookupFeatureMetadataViaApi"))
        self.assertNotIn("translation overlay", app.lower())


if __name__ == "__main__":
    unittest.main()
