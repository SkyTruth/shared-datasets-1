from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class CatalogWebPmtilesJavascriptTests(unittest.TestCase):
    def test_legend_counts_use_unsampled_unique_features_and_refresh_with_tiles(self):
        script = r'''
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync('web/catalog/map-preview.js', 'utf8')
  .replace(/^import .*;\n/gm, '').replace(/^export /gm, '');
vm.runInNewContext(source + `
const layer = {sourceLayer: 'points'};
let features = [];
const context = {map: {querySourceFeatures: () => features}, mapSources: [{sourceId: 'dataset', sourceLayers: [layer]}]};
assert.equal(sampleFieldValues(context, 'label').countsByValue.size, 0);
const point = id => ({id, properties: {feature_id: String(id), label: 'Label'}, geometry: {type: 'Point', coordinates: [0, 0]}});
features = [point(1), point(1)];
let samples = sampleFieldValues(context, 'label');
assert.equal(samples.countsByValue.get('Label'), 1);
assert.equal(samples.values.length, 1);
features = Array.from({length: 1001}, (_, i) => point(i + 1));
features.push(...features);
samples = sampleFieldValues(context, 'label');
assert.equal(samples.countsByValue.get('Label'), 1001);
assert.equal(samples.values.length, SAMPLE_LIMIT);
const mode = {type: 'categorical', field: 'label', values: ['Label'], valueSource: 'tile', countsByValue: new Map([['Label', 1]])};
const firstSignature = colorModeSignature(mode);
mode.countsByValue.set('Label', 2);
assert.notEqual(colorModeSignature(mode), firstSignature);
const byValue = metadataFeatureIdsByValue(new Map([['one', 0], ['two', 0], ['three', 'Other'], ['empty', '']]));
assert.equal(byValue.get('0').size, 2);
assert.equal(byValue.get('Other').size, 1);
assert.equal(byValue.has(''), false);
assert.equal(metadataFeatureIdsByValue(new Map()).size, 0);
assert.equal(layerFilter('Point', {type: 'dataset'}, '').length, 3);
assert.throws(() => layerFilter('Point', {...mode, valueSource: 'metadata', featureIdsByValue: new Map()}, 'Label'), TypeError);
`, {assert});
'''
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

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

    def test_restricted_pmtiles_declares_fetch_credentials(self):
        # The pinned PMTiles API ignores this third argument. Preserve the
        # existing declaration check until the credential behavior is repaired.
        self.assertIn(
            'new window.pmtiles.FetchSource(asset.pmtiles_url, new Headers(), "include")',
            (REPO_ROOT / "web/catalog/map-preview.js").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
