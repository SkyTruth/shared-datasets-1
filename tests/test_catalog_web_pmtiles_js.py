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

    def test_release_metadata_sidecars_drive_inspector_and_colorization(self):
        app = source("web/catalog/app.js")
        map_preview = source("web/catalog/map-preview.js")

        assert_contains_all(
            self,
            app,
            (
                'const DEFAULT_ARTIFACTS_BASE_URL = "https://tiles.skytruth.org/artifacts"',
                "function gsToArtifactUrl",
                "publicFeatureMetadataSidecarUrl(reference, sidecarFile)",
                "privateFeatureMetadataSidecarUrl(assetSlug, release, locale)",
                "function featureMetadataCanLoad",
                "function catalogViewerApiAvailable",
                "Restricted feature metadata requires an authorized catalog viewer or consuming application backend.",
                "files: releaseFiles(files)",
                "function releaseFiles(files)",
                "row_count: release.rows ?? asset.row_count ?? null",
                "function latestVersionForAsset",
                "return latestVersion ? { ...asset, ...latestVersion } : asset",
                "asset.files = Array.isArray(latestVersion.files) ? latestVersion.files : []",
                "Array.isArray(asset?.latest_release?.files)",
                "featureMetadataCache",
                "availableMetadataLocales",
                "metadataLocaleCandidates",
                'const baseLocale = normalized.split("_", 1)[0]',
                "metadataSidecarFileForReference",
                'format: "metadata"',
                'params.set("locale", normalizedLocale)',
                'releaseFilePath(file).endsWith(".metadata.ndjson.gz")',
                'path.endsWith(`.metadata.${normalizedLocale}.ndjson.gz`)',
                "parseFeatureMetadataSidecar",
                "DecompressionStream",
                "item.properties",
                "feature_id: featureId",
                'meta.className = "feature-hit-meta"',
                "geometryHash ? `geom ${geometryHash}` : \"\"",
                "meta.title = `Geometry hash: ${feature.geometryHash}`",
                "function compactGeometryHash",
                "loadFeatureMetadataColorValues",
                "valuesByFeatureId.set(String(featureId), value)",
            ),
        )
        self.assertLess(
            app.index('const selectedField = String(field || "").trim()'),
            app.index("featureMetadataIndex(assetSlug, release, locale)"),
        )
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
            ),
        )
        self.assertNotIn("querySourceLayerFeatures(context.map, source, layer).slice", map_preview)
        self.assertNotIn("TextDecoder", app)
        self.assertNotIn(":lookup", app)
        self.assertNotIn("translation overlay", app.lower())


if __name__ == "__main__":
    unittest.main()
