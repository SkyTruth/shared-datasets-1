from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_snapshot_and_actual_browser_request_functions():
    script = r'''
import fs from 'node:fs';
import assert from 'node:assert/strict';
import vm from 'node:vm';
const source = fs.readFileSync('web/catalog/release-reference.js', 'utf8');
const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const fixture = JSON.parse(fs.readFileSync('tests/fixtures/historical-consumers.json'));
const asset = {...fixture.asset, versions: fixture.index.releases, latest_release: fixture.index.latest_release};
const opts = {bucket:'example-bucket'};
const old = api.selectReleaseReference(asset, '2026-01-01', opts);
assert.match(old.pmtiles_url, /releases\/2026-01-01\/example-layer.pmtiles\?generation=101$/);
assert.match(api.artifactUrl(api.metadataFile(old.files), opts), /generation=102$/);
const latest = api.selectReleaseReference(asset, 'latest', opts);
assert.match(latest.pmtiles_url, /generation=201$/);
assert.throws(() => api.selectReleaseReference(asset, '2020-01-01', opts), /not found/);
const replaced = structuredClone(asset);
replaced.versions[1].files[1].generation = 999;
const replacement = api.selectReleaseReference(replaced, '2026-01-01', opts);
assert.notEqual(api.snapshotKey(old), api.snapshotKey(replacement));
assert.equal(old.pmtiles_file.generation, '101');
assert.ok(Object.isFrozen(old.files[0]));
for (const bad of [true, false, 0, -1, 1.5, '', null, '01', '18446744073709551616', 9007199254740992]) {
  const changed = structuredClone(asset);
  changed.versions[1].files[1].generation = bad;
  assert.throws(() => api.selectReleaseReference(changed, '2026-01-01', opts), /generation/);
}
const bare = structuredClone(asset);
bare.versions[1].files = bare.versions[1].files.filter(f => f.format !== 'metadata');
const bareRef = api.selectReleaseReference(bare, '2026-01-01', opts);
assert.equal(bareRef.date, '2026-01-01');
assert.equal(api.metadataFile(bareRef.files), null);
const ambiguous = structuredClone(asset);
ambiguous.pmtiles_path = ambiguous.pmtiles_path.replace('example-layer.pmtiles', 'unknown.pmtiles');
ambiguous.versions[1].files.push({...ambiguous.versions[1].files[1], path:ambiguous.versions[1].files[1].path.replace('.pmtiles','-points.pmtiles')});
assert.throws(() => api.selectReleaseReference(ambiguous, '2026-01-01', opts), /Ambiguous/);
ambiguous.pmtiles_path = asset.pmtiles_path;
assert.equal(api.selectReleaseReference(ambiguous, '2026-01-01', opts).pmtiles_file.path, old.pmtiles_file.path);
assert.equal(api.selectReleaseReference(fixture.asset, 'latest', opts).files.length, 0);
assert.throws(() => api.selectReleaseReference(fixture.asset, '2026-01-01', opts), /not found/);
assert.equal(api.lookupMatchesReference({asset_slug:asset.slug,resolved_release:old.date,items:[{feature_id:'old1'}]},old),false);
assert.equal(api.lookupMatchesReference({asset_slug:asset.slug,resolved_release:old.date,sidecar_uri:old.files[2].path,sidecar_generation:999},old),false);
assert.equal(api.lookupMatchesReference({asset_slug:asset.slug,resolved_release:old.date,sidecar_uri:old.files[2].path,sidecar_generation:102},old),true);
assert.throws(() => api.assertArtifactResponse({gs_uri:old.pmtiles_file.path,resolved_release:old.date,generation:999},old,old.pmtiles_file),/changed/);

// Exercise the actual orchestration functions, with only transport and DOM absent.
const app = fs.readFileSync('web/catalog/app.js','utf8');
const map = fs.readFileSync('web/catalog/map-preview.js','utf8');
function extract(source,name) {
  const start=source.search(new RegExp(`(?:async )?function ${name}\\(`));
  assert.ok(start>=0, name);
  const tail=source.slice(start);
  const end=tail.slice(1).search(/\n(?:export )?(?:async )?function /);
  return end<0?tail:tail.slice(0,end+1);
}
let requested;
let responsePayload={gs_uri:old.pmtiles_file.path,resolved_release:old.date,generation:'101',pmtiles_url:'https://signed.test/tile?generation=101'};
const context=vm.createContext({...api, URL, URLSearchParams, old, replacement,
  state:{metadataLocale:'', featureMetadataCache:new Map(),featureMetadataRequests:new Map()},
  window:{location:{href:'https://viewer.test/'}},
  fetch:async url=>{requested=String(url);return {ok:true,status:200,json:async()=>responsePayload};}
});
vm.runInContext(['releaseFilesForReference','metadataSidecarFileForReference','featureMetadataCacheKey','normalizeMetadataLocale','mapReferenceKey','downloadUrlRequestUrl'].map(n=>extract(app,n)).join('\n')+'\n'+extract(map,'requestSignedPmtilesUrl')+'\nconst FIELD_SAFE_LOCALE_RE=/^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$/;', context);
assert.equal(vm.runInContext('releaseFilesForReference(old)[2].generation',context),'102');
assert.notEqual(vm.runInContext('mapReferenceKey(old)',context),vm.runInContext('mapReferenceKey(replacement)',context));
await vm.runInContext("requestSignedPmtilesUrl(old,{url:'/api/pmtiles/signed-url',configured:true})",context);
assert.equal(new URL(requested).searchParams.get('version'),old.date);
assert.equal(new URL(requested).searchParams.get('generation'),'101');
responsePayload={...responsePayload,generation:999};
await assert.rejects(vm.runInContext("requestSignedPmtilesUrl(old,{url:'/api/pmtiles/signed-url',configured:true})",context),/changed/);
assert.match(vm.runInContext("downloadUrlRequestUrl(old.slug, old.date, old.canonical_file)",context),/generation=100/);

// Actual hydration rejects malformed history and never filters it into absence.
Object.assign(context,{RELEASE_DATE_RE:/^\d{4}-\d{2}-\d{2}$/, asset});
vm.runInContext(['versionsFromReleaseIndex','releaseFileForFormat','releaseFilePath','releaseFileSha256','releaseFiles','releaseFormats','gsToHttps','basename'].map(n=>extract(app,n)).join('\n'),context);
context.badIndex={...fixture.index};delete context.badIndex.releases;
assert.throws(()=>vm.runInContext('versionsFromReleaseIndex(asset,badIndex)',context),/array/);
context.badIndex=structuredClone(fixture.index);context.badIndex.releases[0].files.push({format:'schema',path:'not-gcs'});
assert.throws(()=>vm.runInContext('versionsFromReleaseIndex(asset,badIndex)',context),/path/);
const legacyMetadata=structuredClone(asset);delete legacyMetadata.versions[1].files[2].generation;delete legacyMetadata.versions[1].files[3].generation;
context.legacyRef=api.selectReleaseReference(legacyMetadata,'2026-01-01',opts);
Object.assign(context,{DEFAULT_SHARED_DATASETS_BUCKET:'example-bucket',isRestrictedAccessTier:()=>false,catalogViewerApiAvailable:()=>true});
vm.runInContext(['publicFeatureMetadataSidecarUrl','publicFeatureMetadataSchemaUrl','releaseSchemaFileForReference','featureMetadataSchemaCanLoad'].map(n=>extract(app,n)).join('\n'),context);
assert.match(context.legacyRef.pmtiles_url,/generation=101/);
assert.equal(vm.runInContext('publicFeatureMetadataSidecarUrl(legacyRef, legacyRef.files[2])',context),'');
assert.equal(vm.runInContext('featureMetadataSchemaCanLoad(legacyRef)',context),false);

// Bounded API data is canonical even with a requested display language.
Object.assign(context,{group:{reference:old,assetSlug:old.slug,release:old.date,locale:'es',ids:new Set(['old1'])}});
vm.runInContext(['lookupFeatureMetadataViaApi','featureMetadataLookupApiUrl','featureMetadataLookupUnavailableStatus'].map(n=>extract(app,n)).join('\n'),context);
responsePayload={asset_slug:old.slug,resolved_release:old.date,sidecar_uri:old.files[2].path,sidecar_generation:102,items:[{feature_id:'old1',found:true,properties:{name:'Canonical name'}}]};
const canonicalResult=await vm.runInContext('lookupFeatureMetadataViaApi(group)',context);
assert.match(canonicalResult.get('old1').metadataLocaleMessage,/Source-language/);
responsePayload={...responsePayload,sidecar_generation:null};
await assert.rejects(vm.runInContext('lookupFeatureMetadataViaApi(group)',context),/snapshot/);

// App-level import awaits are cancelled before they can invoke the map module.
let finishImport;
let mountCount=0, cancelCount=0;
const modulePromise=new Promise(resolve=>{finishImport=resolve;});
const noOp=()=>{};
const node={hidden:false,textContent:'',replaceChildren:noOp};
const renderContext=vm.createContext({state:{mapRequestSerial:0,featureLookupSerial:0,selectedSlugs:['example-layer']},old,
 document:{querySelector:()=>node}, elements:{mapSection:{},mapStatus:{},pmtilesRow:{},pmtiles:{},},
 loadMapModule:()=>modulePromise, setZoomSelectionEnabled:noOp,clearFeatureInspector:noOp,clearColorLegend:noOp,
 resetMetadataLanguageControl:noOp,renderMetadataSidecarPath:noOp,resetColorizeControl:noOp,resetLayerControl:noOp,
 withPmtilesCacheBust:a=>a,selectedLayerAsset:()=>old,prepareLayerControl:noOp,selectedColorizeAsset:()=>old,
 prepareColorizeControl:noOp,selectedMetadataLanguageAsset:()=>old,prepareMetadataLanguageControl:noOp,
 updateLayerOptions:noOp,updateColorizeFields:noOp,clearUnavailableColorField:noOp,renderColorLegend:noOp,
 handleFeatureSelect:noOp,loadFeatureMetadataColorValues:noOp,mapUnavailableMessage:e=>String(e)});
vm.runInContext(extract(app,'renderPmtiles'),renderContext);
const pending=vm.runInContext('renderPmtiles([old])',renderContext);
await vm.runInContext('renderPmtiles([{release_error:"Missing release"}])',renderContext);
finishImport({renderMapPreview:()=>{mountCount++;},cancelMapPreview:()=>{cancelCount++;}});
await pending;
assert.equal(mountCount,0);
assert.match(renderContext.elements.mapStatus.textContent,/Missing release/);
renderContext.state.mapModule={cancelMapPreview:()=>{cancelCount++;}};
await vm.runInContext('renderPmtiles([])',renderContext);
assert.equal(cancelCount,1);

// Empty selection also cancels an import before the map module is available.
renderContext.state.mapModule=null;
let finishEmptyImport;
renderContext.loadMapModule=()=>new Promise(resolve=>{finishEmptyImport=resolve;});
const beforeEmpty=vm.runInContext('renderPmtiles([old])',renderContext);
await vm.runInContext('renderPmtiles([])',renderContext);
finishEmptyImport({renderMapPreview:()=>{mountCount++;}});await beforeEmpty;
assert.equal(mountCount,0);

// Actual inspector completion cannot restore a cleared error/empty selection.
renderContext.elements.featureInspector={hidden:false,replaceChildren:noOp};
let finishInspection;
const inspectionRenders=[];
renderContext.renderFeatureInspector=features=>inspectionRenders.push(features);
renderContext.enrichFeatureMetadata=()=>new Promise(resolve=>{finishInspection=resolve;});
vm.runInContext(['handleFeatureSelect','clearFeatureInspector'].map(n=>extract(app,n)).join('\n'),renderContext);
for (const next of [[],[{release_error:'Missing release'}]]) {
 renderContext.next=next;
 const count=inspectionRenders.length;
 const pendingInspection=vm.runInContext('handleFeatureSelect([{assetSlug:"example-layer",properties:{feature_id:"old1"}}])',renderContext);
 await vm.runInContext('renderPmtiles(next)',renderContext);
 finishInspection([{properties:{feature_id:'old1',name:'Stale'}}]);await pendingInspection;
 assert.equal(inspectionRenders.length,count+1);
 assert.equal(renderContext.state.inspectedFeatures.length,0);
 assert.equal(renderContext.elements.featureInspector.hidden,true);
}

// Map-module cancellation invalidates the render before delayed libraries finish.
let finishLibraries;
const libraries=new Promise(resolve=>{finishLibraries=resolve;});
const mapContext=vm.createContext({old,loadDependencies:()=>libraries,clearActiveMap:noOp,
 installProtocol:()=>{throw new Error('stale render reached protocol installation');}});
vm.runInContext('let activeRenderSerial=0;\n'+['renderIsCurrent','cancelMapPreview','renderMapPreview'].map(n=>extract(map,n)).join('\n'),mapContext);
mapContext.container={replaceChildren:noOp};mapContext.status={};
const pendingMap=vm.runInContext('renderMapPreview({assets:[old],container,status})',mapContext);
vm.runInContext('cancelMapPreview()',mapContext);finishLibraries();await pendingMap;

console.log('release snapshots, replacements, unverified lookup, actual browser requests: passed');
'''
    result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
