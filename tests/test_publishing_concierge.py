from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import publishing_concierge


CATEGORIES_YAML = """categories:
  "100-geographic-reference":
    subcategories:
      "110-boundaries": "Boundaries."
  "300-infrastructure-industrial":
    subcategories:
      "330-offshore-platforms": "Offshore platforms."
"""


class PublishingConciergeTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: dict) -> Path:
        path.write_text(json.dumps(payload))
        return path

    def _start_workflow(
        self,
        root: Path,
        *,
        canonical_format: str | None = "csv",
        release_date: str | None = None,
        request_classification: str = "canonical-publish",
        preview_ref: str = "feat/test-preview",
    ) -> Path:
        categories = root / "categories.yaml"
        categories.write_text(CATEGORIES_YAML)
        source = root / "example.csv"
        source.write_text("source_id,NAME\nA1,North Reef\n")
        args = [
            "start",
            str(source),
            "--asset-slug",
            "example",
            "--title",
            "Example",
            "--category",
            "300-infrastructure-industrial",
            "--subcategory",
            "330-offshore-platforms",
            "--source-name",
            "Example source",
            "--license",
            "Example license",
            "--citation",
            "Example citation",
            "--access-tier",
            "private",
            "--request-classification",
            request_classification,
            "--proposal-id",
            "pr-123",
            "--categories",
            str(categories),
            "--docs-dir",
            str(root / "docs/assets"),
        ]
        if canonical_format:
            args.extend(["--canonical-format", canonical_format])
        if release_date:
            args.extend(["--release-date", release_date])
        if request_classification == "preview-only":
            args.extend(["--preview-ref", preview_ref])
        with mock.patch.dict(publishing_concierge.os.environ, {"SHARED_DATASETS_WORKDIR": str(root / "work")}):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(args)
        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        return Path(payload["state_file"])

    def _complete_preview_csv_workflow_through_validate(self, root: Path, state_file: Path) -> None:
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "resolve-metadata",
                {
                    "source_name": "Example source",
                    "license": "Example license",
                    "citation": "Example citation",
                    "steward": "Data Steward",
                    "source_version_date": "2026-05-01",
                    "update_cadence": "manual",
                    "intended_consumers": ["test"],
                    "shared_datasets_rationale": "Disposable preview load for catalog QA.",
                    "alternatives_considered": "Production publish path.",
                    "deprecation_exit_policy": "Preview data will be replaced or destroyed with the preview slot.",
                    "estimated_published_footprint": "1 MB",
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "settle-contract",
                {
                    "confirmed_asset_slug": "example",
                    "confirmed_category": "300-infrastructure-industrial",
                    "confirmed_subcategory": "330-offshore-platforms",
                    "confirmed_canonical_format": "csv",
                    "release_layout": "versioned",
                    "access_tier": "private",
                    "exception_flags": {
                        "public_access_approved": False,
                        "new_top_level_category_approved": False,
                        "new_canonical_format_approved": False,
                        "large_data_exception_approved": False,
                        "incompatible_schema_change_approved": False,
                        "move_or_delete_releases_approved": False,
                        "unsafe_overwrite_approved": False,
                        "infrastructure_mutation_approved": False,
                    },
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "profile-fields",
                {
                    "decision_table_present": True,
                    "profile_scope": "full",
                    "provider_id_decision": "use-provider-id",
                    "provider_id_fields": ["source_id"],
                    "generated_group_id_decision": "not-needed",
                    "group_id_fields": [],
                    "generated_row_id_decision": "not-needed",
                    "ext_id_decision": "provider-id",
                    "ext_id_fields": ["source_id"],
                    "search_fields": ["NAME"],
                },
            ),
            0,
        )
        artifact = root / "work/vector-assets/example/publish/example.csv"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("source_id,NAME\nA1,North Reef\n")
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "build-artifacts",
                {"artifacts": [{"path": str(artifact), "format": "csv", "role": "canonical"}]},
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "validate-artifacts",
                {
                    "commands_run": ["csv validation"],
                    "validation_summary": "CSV is geometry-free and row count matches.",
                    "all_passed": True,
                    "tool_versions": {"csv-validator": "not applicable; inspected with Python csv"},
                },
            ),
            0,
        )

    def _complete_preview_fgb_workflow_through_build(self, root: Path, state_file: Path) -> None:
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "resolve-metadata",
                {
                    "source_name": "Example source",
                    "license": "Example license",
                    "citation": "Example citation",
                    "steward": "Data Steward",
                    "source_version_date": "2026-05-01",
                    "update_cadence": "manual",
                    "intended_consumers": ["test"],
                    "shared_datasets_rationale": "Disposable vector preview load for catalog QA.",
                    "alternatives_considered": "Production publish path.",
                    "deprecation_exit_policy": "Preview data will be replaced or destroyed with the preview slot.",
                    "estimated_published_footprint": "1 MB",
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "settle-contract",
                {
                    "confirmed_asset_slug": "example",
                    "confirmed_category": "300-infrastructure-industrial",
                    "confirmed_subcategory": "330-offshore-platforms",
                    "confirmed_canonical_format": "fgb",
                    "release_layout": "versioned",
                    "access_tier": "private",
                    "exception_flags": {
                        "public_access_approved": False,
                        "new_top_level_category_approved": False,
                        "new_canonical_format_approved": False,
                        "large_data_exception_approved": False,
                        "incompatible_schema_change_approved": False,
                        "move_or_delete_releases_approved": False,
                        "unsafe_overwrite_approved": False,
                        "infrastructure_mutation_approved": False,
                    },
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "profile-fields",
                {
                    "decision_table_present": True,
                    "profile_scope": "full",
                    "provider_id_decision": "none-suitable",
                    "provider_id_fields": [],
                    "generated_group_id_decision": "not-needed",
                    "group_id_fields": [],
                    "generated_row_id_decision": "approved",
                    "ext_id_decision": "feature-id",
                    "ext_id_fields": [],
                    "search_fields": ["NAME"],
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "translation-decision",
                {"decision": "none", "locales": [], "fields": []},
            ),
            0,
        )
        publish_dir = root / "work/vector-assets/example/publish"
        publish_dir.mkdir(parents=True)
        artifacts = [
            ("example.fgb", "fgb", "canonical"),
            ("example.pmtiles", "pmtiles", "pmtiles"),
            ("example.metadata.ndjson.gz", "metadata_sidecar_v1", "feature-metadata-sidecar"),
            ("example.schema.json", "release_schema_v1", "schema"),
            ("example.manifest.json", "release_manifest_v1", "manifest"),
        ]
        evidence_artifacts = []
        for filename, fmt, role in artifacts:
            artifact = publish_dir / filename
            artifact.write_text("{}")
            evidence_artifacts.append({"path": str(artifact), "format": fmt, "role": role})
        self.assertEqual(
            self._confirm(root, state_file, "build-artifacts", {"artifacts": evidence_artifacts}),
            0,
        )

    def test_translation_decision_rejects_deferred(self):
        with self.assertRaisesRegex(publishing_concierge.WorkflowError, "autogenerate or none"):
            publishing_concierge.validate_translation_decision(
                {},
                {"decision": "deferred", "locales": [], "fields": []},
            )

        step = next(step for step in publishing_concierge.STEP_DEFINITIONS if step.step_id == "translation-decision")
        self.assertEqual(step.evidence_schema["decision"], "autogenerate|none")

    def test_build_artifacts_requires_all_autogenerated_localized_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish_dir = Path(tmp)
            files = [
                ("example.fgb", "fgb", "canonical"),
                ("example.pmtiles", "pmtiles", "pmtiles"),
                ("example.metadata.ndjson.gz", "metadata_sidecar_v1", "feature-metadata-sidecar"),
                ("example.schema.json", "release_schema_v1", "schema"),
                ("example.manifest.json", "release_manifest_v1", "manifest"),
                ("example.metadata-translations.csv", "metadata_translations_csv_v1", "metadata-translations"),
                ("example.metadata.es.ndjson.gz", "localized_metadata_sidecar_v1", "localized-metadata-sidecar"),
            ]
            artifacts = []
            for filename, fmt, role in files:
                path = publish_dir / filename
                path.write_text("{}")
                artifacts.append({"path": str(path), "format": fmt, "role": role})

            with self.assertRaisesRegex(publishing_concierge.WorkflowError, "localized_metadata_sidecar_v1:fr"):
                publishing_concierge.validate_build_artifacts(
                    self._autogenerated_localized_state(),
                    {"artifacts": artifacts},
                )

    def test_build_artifacts_captures_all_autogenerated_localized_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            publish_dir = Path(tmp)
            files = [
                ("example.fgb", "fgb", "canonical"),
                ("example.pmtiles", "pmtiles", "pmtiles"),
                ("example.metadata.ndjson.gz", "metadata_sidecar_v1", "feature-metadata-sidecar"),
                ("example.schema.json", "release_schema_v1", "schema"),
                ("example.manifest.json", "release_manifest_v1", "manifest"),
                ("example.metadata-translations.csv", "metadata_translations_csv_v1", "metadata-translations"),
                ("example.metadata.es.ndjson.gz", "localized_metadata_sidecar_v1", "localized-metadata-sidecar"),
                ("example.metadata.fr.ndjson.gz", "localized_metadata_sidecar_v1", "localized-metadata-sidecar"),
            ]
            artifacts = []
            for filename, fmt, role in files:
                path = publish_dir / filename
                path.write_text("{}")
                artifacts.append({"path": str(path), "format": fmt, "role": role})

            normalized = publishing_concierge.validate_build_artifacts(
                self._autogenerated_localized_state(),
                {"artifacts": artifacts},
            )

        locales = sorted(
            artifact["locale"]
            for artifact in normalized["artifacts"]
            if artifact["role"] == "localized-metadata-sidecar"
        )
        self.assertEqual(locales, ["es", "fr"])

    def _fgb_validation_payload(self, *, include_gdal: bool = True) -> dict:
        payload = {
            "commands_run": [
                "ogrinfo -ro -al -so example.fgb",
                "pmtiles verify example.pmtiles",
                "pmtiles show example.pmtiles",
            ],
            "validation_summary": "FGB schema and PMTiles display artifact validated.",
            "all_passed": True,
            "tool_versions": {
                "ogr2ogr": "/usr/bin/ogr2ogr GDAL 3.8.0",
                "ogrinfo": "/usr/bin/ogrinfo GDAL 3.8.0",
                "pmtiles": "/usr/local/bin/pmtiles 1.26.1",
            },
            "pmtiles": {
                "magic_bytes_confirmed": True,
                "verify_passed": True,
                "show_inspected": True,
                "decoded_tile_checked": True,
            },
        }
        if include_gdal:
            payload["gdal"] = {
                "ogr2ogr": "/usr/bin/ogr2ogr GDAL 3.8.0",
                "ogrinfo": "/usr/bin/ogrinfo GDAL 3.8.0",
                "ogrinfo_summary_passed": True,
                "feature_count_checked": True,
                "geometry_type_checked": True,
                "crs_checked": True,
                "field_schema_checked": True,
            }
        return payload

    def _preview_fgb_uploaded_objects(self) -> list[dict]:
        no_cache = publishing_concierge.no_cache_control()
        release_prefix = (
            "gs://skytruth-shared-datasets-1-preview/"
            "300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/"
        )
        return [
            {
                "uri": f"{release_prefix}example.fgb",
                "generation": "111",
                "role": "canonical",
                "content_type": "application/octet-stream",
            },
            {
                "uri": f"{release_prefix}example.pmtiles",
                "generation": "112",
                "role": "pmtiles",
                "content_type": "application/vnd.pmtiles",
                "cache_control": no_cache,
            },
            {
                "uri": f"{release_prefix}example.metadata.ndjson.gz",
                "generation": "113",
                "role": "feature-metadata-sidecar",
                "content_type": "application/x-ndjson",
                "cache_control": no_cache,
            },
            {
                "uri": f"{release_prefix}example.schema.json",
                "generation": "114",
                "role": "schema",
                "content_type": "application/json",
                "cache_control": no_cache,
            },
            {
                "uri": f"{release_prefix}example.manifest.json",
                "generation": "115",
                "role": "manifest",
                "content_type": "application/json",
                "cache_control": no_cache,
            },
            {
                "uri": "gs://skytruth-shared-datasets-1-preview/_catalog/releases/example.json",
                "generation": "116",
                "role": "release-index",
                "content_type": "application/json",
                "cache_control": no_cache,
            },
            {
                "uri": (
                    "gs://skytruth-shared-datasets-1-preview/"
                    "300-infrastructure-industrial/330-offshore-platforms/example/runs/2026-05-01.json"
                ),
                "generation": "117",
                "role": "run-record",
                "content_type": "application/json",
                "cache_control": no_cache,
            },
        ]

    def _complete_preview_fgb_workflow_through_validate(self, root: Path, state_file: Path) -> None:
        self._complete_preview_fgb_workflow_through_build(root, state_file)
        self.assertEqual(
            self._confirm(root, state_file, "validate-artifacts", self._fgb_validation_payload()),
            0,
        )

    def _complete_preview_fgb_workflow_through_upload(self, root: Path, state_file: Path) -> None:
        self._complete_preview_fgb_workflow_through_validate(root, state_file)
        self.assertEqual(
            self._confirm(root, state_file, "preview-upload", {"uploaded_objects": self._preview_fgb_uploaded_objects()}),
            0,
        )

    def _preview_load_payload(self) -> dict:
        return {
            "workflow_name": "feature-preview-index-load.yml",
            "workflow_run_url": "https://github.com/skytruth/shared-datasets/actions/runs/123",
            "status": "success",
            "dispatched_ref": "feat/test-preview",
            "workflow_inputs_checked_against_preview_ref": True,
            "asset_slug": "example",
            "release": "2026-05-01",
            "inputs": {
                "sidecar_uri": (
                    "gs://skytruth-shared-datasets-1-preview/"
                    "300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/"
                    "example.metadata.ndjson.gz"
                ),
                "sidecar_generation": "113",
                "schema_uri": (
                    "gs://skytruth-shared-datasets-1-preview/"
                    "300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/"
                    "example.schema.json"
                ),
                "schema_generation": "114",
                "manifest_uri": (
                    "gs://skytruth-shared-datasets-1-preview/"
                    "300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/"
                    "example.manifest.json"
                ),
                "manifest_generation": "115",
            },
            "viewer_refresh_verified": True,
        }

    def _autogenerated_localized_state(self) -> dict:
        return {
            "plan": {
                "asset_slug": "example",
                "asset_root": "300-infrastructure-industrial/330-offshore-platforms/example",
                "canonical_format": "fgb",
                "available_formats": ["fgb", "pmtiles"],
                "release_date": "2026-05-01",
            },
            "steps": {
                "translation-decision": {
                    "status": "completed",
                    "evidence": {"decision": "autogenerate", "locales": ["es", "fr"], "fields": ["name"]},
                }
            },
        }

    def _preview_csv_uploaded_objects(self) -> list[dict]:
        no_cache = publishing_concierge.no_cache_control()
        release_prefix = (
            "gs://skytruth-shared-datasets-1-preview/"
            "300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/"
        )
        return [
            {
                "uri": f"{release_prefix}example.csv",
                "generation": "111",
                "role": "canonical",
                "content_type": "text/csv",
            },
            {
                "uri": "gs://skytruth-shared-datasets-1-preview/_catalog/releases/example.json",
                "generation": "112",
                "role": "release-index",
                "content_type": "application/json",
                "cache_control": no_cache,
            },
            {
                "uri": (
                    "gs://skytruth-shared-datasets-1-preview/"
                    "300-infrastructure-industrial/330-offshore-platforms/example/runs/2026-05-01.json"
                ),
                "generation": "113",
                "role": "run-record",
                "content_type": "application/json",
                "cache_control": no_cache,
            },
        ]

    def _preview_catalog_refresh_payload(self) -> dict:
        return {
            "workflow_name": "feature-preview-deploy.yml",
            "workflow_run_url": "https://github.com/skytruth/shared-datasets/actions/runs/456",
            "workflow_run_id": "456",
            "dispatched_ref": "feat/test-preview",
            "preview_data_mode": "preserve",
            "conclusion": "success",
            "catalog_json_uri": "gs://skytruth-shared-datasets-1-preview/_catalog/web/catalog.json",
            "catalog_json_generation": "211",
            "catalog_json_updated_at": "2026-06-04T13:36:47Z",
            "catalog_generated_at": "2026-06-04T13:36:42Z",
        }

    def _preview_catalog_asset_payload(self, uploaded_objects: list[dict]) -> dict:
        release_files = [
            {
                "path": obj["uri"],
                "generation": int(obj["generation"]),
                "role": obj["role"],
                "format": obj["role"],
            }
            for obj in uploaded_objects
            if obj["role"] not in {"release-index", "run-record"}
        ]
        has_pmtiles = any(obj["role"] == "pmtiles" for obj in uploaded_objects)
        asset = {
            "slug": "example",
            "title": "Example",
            "has_pmtiles": has_pmtiles,
            "versions": [
                {
                    "date": "2026-05-01",
                    "files": release_files,
                }
            ],
        }
        if has_pmtiles:
            asset["pmtiles_path"] = next(obj["uri"] for obj in uploaded_objects if obj["role"] == "pmtiles")
        return {
            "catalog_json_uri": "gs://skytruth-shared-datasets-1-preview/_catalog/web/catalog.json",
            "catalog_json_generation": "211",
            "asset_slug_present": True,
            "asset_count": 1,
            "catalog_asset": asset,
        }

    def _confirm(self, root: Path, state_file: Path, step: str, payload: dict) -> int:
        evidence = self._write_json(root / f"{step}.json", payload)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return publishing_concierge.main(
                [
                    "confirm",
                    "--state-file",
                    str(state_file),
                    "--step",
                    step,
                    "--evidence-json",
                    str(evidence),
                ]
            )

    def _complete_first_csv_workflow_through_pr_ready(self, root: Path, state_file: Path) -> None:
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "resolve-metadata",
                {
                    "source_name": "Example source",
                    "license": "Example license",
                    "citation": "Example citation",
                    "steward": "Data Steward",
                    "source_version_date": "2026-05-01",
                    "update_cadence": "manual",
                    "intended_consumers": ["test"],
                    "shared_datasets_rationale": "Reusable reference table for multiple projects.",
                    "alternatives_considered": "Project storage and direct upstream access.",
                    "deprecation_exit_policy": "Deprecate with a successor if source support ends.",
                    "estimated_published_footprint": "1 MB",
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "settle-contract",
                {
                    "confirmed_asset_slug": "example",
                    "confirmed_category": "300-infrastructure-industrial",
                    "confirmed_subcategory": "330-offshore-platforms",
                    "confirmed_canonical_format": "csv",
                    "release_layout": "latest-only",
                    "access_tier": "private",
                    "exception_flags": {
                        "public_access_approved": False,
                        "new_top_level_category_approved": False,
                        "new_canonical_format_approved": False,
                        "large_data_exception_approved": False,
                        "incompatible_schema_change_approved": False,
                        "move_or_delete_releases_approved": False,
                        "unsafe_overwrite_approved": False,
                        "infrastructure_mutation_approved": False,
                    },
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "profile-fields",
                {
                    "decision_table_present": True,
                    "profile_scope": "full",
                    "provider_id_decision": "use-provider-id",
                    "provider_id_fields": ["source_id"],
                    "generated_group_id_decision": "not-needed",
                    "group_id_fields": [],
                    "generated_row_id_decision": "not-needed",
                    "ext_id_decision": "provider-id",
                    "ext_id_fields": ["source_id"],
                    "search_fields": ["NAME"],
                },
            ),
            0,
        )
        artifact = root / "work/vector-assets/example/publish/example.csv"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("source_id,NAME\nA1,North Reef\n")
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "build-artifacts",
                {"artifacts": [{"path": str(artifact), "format": "csv", "role": "canonical"}]},
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "validate-artifacts",
                {
                    "commands_run": ["csv validation"],
                    "validation_summary": "CSV is geometry-free and row count matches.",
                    "all_passed": True,
                    "tool_versions": {"csv-validator": "not applicable; inspected with Python csv"},
                },
            ),
            0,
        )
        doc = root / "docs/assets/example.md"
        doc.parent.mkdir(parents=True)
        doc.write_text(
            "---\n"
            "asset_slug: example\n"
            "title: Example\n"
            "category: 300-infrastructure-industrial\n"
            "subcategory: 330-offshore-platforms\n"
            "status: active\n"
            "access_tier: private\n"
            "owner: SkyTruth\n"
            "update_cadence: manual\n"
            "canonical_format: csv\n"
            "canonical_file: latest/example.csv\n"
            "available_formats: [csv]\n"
            "metadata_paths: [README.md]\n"
            "source: Example source\n"
            "license: Example license\n"
            "citation: Example citation\n"
            "---\n\n# Example\n"
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "document-asset",
                {
                    "asset_doc_path": str(doc),
                    "admission_complete": True,
                    "source_license_citation_complete": True,
                    "schema_or_properties_complete": True,
                    "data_profile_complete": True,
                },
            ),
            0,
        )
        readmes = root / "work/readmes"
        readmes.mkdir(parents=True)
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "catalog-outputs",
                {
                    "generate_ran": True,
                    "check_passed": True,
                    "readmes_exported": True,
                    "readmes_dir": str(readmes),
                },
            ),
            0,
        )
        catalog_json = root / "work/catalog-web/catalog.json"
        catalog_json.parent.mkdir(parents=True)
        catalog_json.write_text("{}")
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "catalog-web",
                {
                    "built": True,
                    "catalog_json_path": str(catalog_json),
                    "content_type": "application/json",
                    "cache_control": "no-cache, max-age=0, must-revalidate",
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "stage-scratch",
                {
                    "staged_objects": [
                        {
                            "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example/pr-123/example.csv",
                            "source_generation": "111",
                            "destination_uri": "gs://skytruth-shared-datasets-1/300-infrastructure-industrial/330-offshore-platforms/example/latest/example.csv",
                            "content_type": "text/csv",
                            "cache_control": "",
                        },
                        {
                            "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example/pr-123/catalog.json",
                            "source_generation": "112",
                            "destination_uri": "gs://skytruth-shared-datasets-1/_catalog/web/catalog.json",
                            "content_type": "application/json",
                            "cache_control": "no-cache, max-age=0, must-revalidate",
                        },
                    ]
                },
            ),
            0,
        )
        self.assertEqual(
            self._confirm(
                root,
                state_file,
                "stat-destinations",
                {
                    "destinations": [
                        {
                            "destination_uri": "gs://skytruth-shared-datasets-1/300-infrastructure-industrial/330-offshore-platforms/example/latest/example.csv",
                            "destination_generation": "",
                            "status": "absent",
                        },
                        {
                            "destination_uri": "gs://skytruth-shared-datasets-1/_catalog/web/catalog.json",
                            "destination_generation": "222",
                            "status": "exists",
                        },
                    ]
                },
            ),
            0,
        )

    def test_plan_infers_vector_build_and_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date="2026-05-01",
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.canonical_format, "fgb")
        self.assertEqual(plan.available_formats, ["fgb", "pmtiles"])
        self.assertEqual(plan.asset_root, "100-geographic-reference/110-boundaries/example-asset")
        self.assertEqual(
            plan.canonical_path,
            "gs://example-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb",
        )
        self.assertTrue(any("vector_asset.py build" in command for command in plan.suggested_commands))
        self.assertTrue(any("--maxzoom auto" in command for command in plan.suggested_commands))
        self.assertTrue(any("publish-release" in command for command in plan.remote_write_commands))
        self.assertEqual(plan.blocking_questions, [])
        self.assertFalse(any("PMTiles is automatic" in note for note in plan.notes))
        self.assertTrue(any("require a PMTiles companion" in note for note in plan.notes))
        self.assertTrue(any("resolved after the canonical FGB" in note for note in plan.notes))
        self.assertTrue(any("shared_datasets_group_id" in note for note in plan.notes))

    def test_missing_source_and_license_are_blocking_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("id,name\n1,A\n")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug=None,
                title=None,
                category="300-infrastructure-industrial",
                subcategory="330-offshore-platforms",
                owner="SkyTruth",
                source_name=None,
                license_text=None,
                citation=None,
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.asset_slug, "example")
        self.assertEqual(plan.canonical_format, "csv")
        self.assertEqual(plan.available_formats, ["csv"])
        self.assertIn("Confirm source name or URL.", plan.blocking_questions)
        self.assertIn("Confirm license or terms.", plan.blocking_questions)
        self.assertIn("Confirm citation for the original source publication.", plan.blocking_questions)
        self.assertTrue(any("geometry-free" in note for note in plan.notes))

    def test_plan_rejects_noncanonical_format_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.fgb"
            source.write_text("placeholder")

            with self.assertRaisesRegex(publishing_concierge.ConciergeError, "unsupported canonical format"):
                publishing_concierge.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    title="Example Asset",
                    category="100-geographic-reference",
                    subcategory="110-boundaries",
                    owner="SkyTruth",
                    source_name="Example source",
                    license_text="Example license",
                    citation="Example citation",
                    update_cadence="manual",
                    canonical_format="flatgeobuf",
                    access_tier="public",
                    bucket="example-bucket",
                    release_date="2026-05-01",
                    categories_path=categories,
                    docs_dir=root / "docs/assets",
                )

    def test_curator_field_options_profile_csv_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text(
                "source_id,NAME,GIS_AREA_K\n"
                "A1,North Reef,10\n"
                "A2,North Reef,11\n"
                "A3,South Reef,12\n"
            )

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example",
                title="Example",
                category="300-infrastructure-industrial",
                subcategory="330-offshore-platforms",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.curator_field_options.id_field_candidates[0].field, "source_id")
        self.assertEqual(plan.curator_field_options.id_field_candidates[0].confidence, "high")
        self.assertEqual(plan.curator_field_options.group_field_candidates[0].field, "NAME")
        self.assertEqual(plan.curator_field_options.group_field_candidates[0].distinct_values, 2)
        self.assertFalse(plan.curator_field_options.generated_row_id_option.available)
        self.assertFalse(any(candidate.field == "GIS_AREA_K" for candidate in plan.curator_field_options.group_field_candidates))

    def test_field_profile_reports_decision_table_statistics(self):
        options = publishing_concierge.profile_rows(
            [
                {"source_id": "A1", "NAME": "North Reef", "DISC": "-9999"},
                {"source_id": "A2", "NAME": "North Reef", "DISC": "1999"},
                {"source_id": "A3", "NAME": "South Reef", "DISC": ""},
            ]
        )

        self.assertEqual(options.total_rows, 3)
        self.assertEqual(options.total_columns, 3)
        provider = options.id_field_candidates[0]
        self.assertEqual(provider.field, "source_id")
        self.assertEqual(provider.datatype, "string")
        self.assertEqual(provider.distinction_percent, 100.0)
        self.assertEqual(provider.emptiness_percent, 0.0)
        self.assertEqual(provider.domination_percent, 33.33)
        self.assertEqual(provider.skew_ratio, 1.0)
        self.assertEqual(provider.top_examples[0].value, "A1")

        name = options.group_field_candidates[0]
        self.assertEqual(name.field, "NAME")
        self.assertEqual(name.distinction_percent, 66.67)
        self.assertEqual(name.domination_percent, 66.67)
        self.assertEqual(name.skew_ratio, 1.33)
        self.assertTrue(any("top value" in concern for concern in name.concerns))

        disc_profile = next(profile for profile in options.all_fields_profile if profile.name == "DISC")
        self.assertEqual(disc_profile.datatype, "integer")
        self.assertEqual(disc_profile.empty_values, 1)
        self.assertEqual(disc_profile.sentinel_value_count, 1)

    def test_profile_field_evidence_accepts_feature_id_ext_id_fallback(self):
        state = {}

        normalized = publishing_concierge.validate_profile_fields(
            state,
            {
                "decision_table_present": True,
                "profile_scope": "full",
                "provider_id_decision": "none-suitable",
                "provider_id_fields": [],
                "generated_group_id_decision": "not-needed",
                "group_id_fields": [],
                "generated_row_id_decision": "approved",
                "ext_id_decision": "feature-id",
                "ext_id_fields": [],
                "search_fields": ["NAME"],
            },
        )

        self.assertEqual(normalized["ext_id_decision"], "feature-id")
        self.assertEqual(normalized["ext_id_fields"], [])

    def test_profile_field_evidence_rejects_deferred_decisions(self):
        base_evidence = {
            "decision_table_present": True,
            "profile_scope": "full",
            "provider_id_decision": "none-suitable",
            "provider_id_fields": [],
            "generated_group_id_decision": "not-needed",
            "group_id_fields": [],
            "generated_row_id_decision": "rejected",
            "ext_id_decision": "feature-id",
            "ext_id_fields": [],
            "search_fields": [],
        }

        for field_name, message in (
            ("provider_id_decision", "use-provider-id or none-suitable"),
            ("generated_group_id_decision", "not-needed or approved"),
            ("generated_row_id_decision", "not-needed, approved, or rejected"),
        ):
            evidence = {**base_evidence, field_name: "deferred"}
            with self.subTest(field_name=field_name), self.assertRaisesRegex(
                publishing_concierge.WorkflowError,
                message,
            ):
                publishing_concierge.validate_profile_fields({}, evidence)

        step = next(step for step in publishing_concierge.STEP_DEFINITIONS if step.step_id == "profile-fields")
        self.assertEqual(step.evidence_schema["provider_id_decision"], "use-provider-id|none-suitable")
        self.assertEqual(step.evidence_schema["generated_group_id_decision"], "not-needed|approved")
        self.assertEqual(step.evidence_schema["generated_row_id_decision"], "not-needed|approved|rejected")

    def test_petrodata_like_recommendations_keep_table_compact(self):
        rows = []
        for index in range(102):
            rows.append(
                {
                    "PRIMKEY": "AL001PET" if index in {0, 1, 2} else f"PET{index:03d}",
                    "NAME": "West Siberian Basin" if index < 20 else f"Basin {index % 12}",
                    "COUNTRY": "Russia" if index < 30 else ["United States", "Brazil", "Canada"][index % 3],
                    "RESINFO": ["oil and gas", "gas", "oil"][index % 3],
                    "source_layer": "onshore" if index < 80 else "offshore",
                    "LAT": str(1.0 + index),
                    "LONG": str(2.0 + index),
                    "SOURCEINFO": f"Long reference text {index % 5}",
                }
            )

        options = publishing_concierge.profile_rows(rows)

        provider = options.id_field_candidates[0]
        self.assertEqual(provider.field, "PRIMKEY")
        self.assertEqual(provider.confidence, "high")
        self.assertTrue(any("duplicate value" in concern for concern in provider.concerns))
        group_fields = [candidate.field for candidate in options.group_field_candidates]
        self.assertIn("NAME", group_fields)
        self.assertIn("COUNTRY", group_fields)
        self.assertIn("RESINFO", group_fields)
        self.assertIn("source_layer", group_fields)
        self.assertNotIn("LAT", group_fields)
        self.assertNotIn("LONG", group_fields)
        self.assertNotIn("SOURCEINFO", group_fields)

    def test_coral_like_name_fields_surface_domination_warning(self):
        rows = (
            [{"NAME": "Not Reported", "ORIG_NAME": "Not Reported"} for _ in range(900)]
            + [{"NAME": f"Reef {index}", "ORIG_NAME": f"Original Reef {index}"} for index in range(100)]
        )

        options = publishing_concierge.profile_rows(rows)

        by_field = {candidate.field: candidate for candidate in options.group_field_candidates}
        self.assertIn("NAME", by_field)
        self.assertIn("ORIG_NAME", by_field)
        self.assertGreaterEqual(by_field["NAME"].domination_percent or 0, 80)
        self.assertGreaterEqual(by_field["NAME"].skew_ratio or 0, 25)
        self.assertTrue(any("top value" in concern for concern in by_field["NAME"].concerns))
        self.assertTrue(any(example.value == "Not Reported" and example.is_sentinel for example in by_field["NAME"].top_examples))

    def test_curator_field_options_profiles_full_csv_under_sample_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("source_id,NAME\nA1,North Reef\nA2,North Reef\n")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example",
                title="Example",
                category="300-infrastructure-industrial",
                subcategory="330-offshore-platforms",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.curator_field_options.profile_scope, "full")
        self.assertEqual(plan.curator_field_options.total_rows, 2)
        self.assertEqual(plan.curator_field_options.profiled_row_count, 2)
        self.assertEqual(plan.curator_field_options.id_field_candidates[0].field, "source_id")

    def test_profile_row_iter_uses_deterministic_random_sample_not_first_rows(self):
        rows = [{"source_id": f"A{index}", "NAME": f"Name {index}"} for index in range(25)]

        sample, total_rows, profile_scope = publishing_concierge.profile_row_iter(rows, sample_size=10, random_seed=7)

        self.assertEqual(total_rows, 25)
        self.assertEqual(profile_scope, "random_sample")
        self.assertEqual(len(sample), 10)
        self.assertNotEqual([row["source_id"] for row in sample], [f"A{index}" for index in range(10)])

    def test_curator_field_options_profile_ogr_vector_source_before_group_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.fgb"
            source.write_text("placeholder")

            with mock.patch.dict(publishing_concierge.os.environ, {"SHARED_DATASETS_PROFILE_WITH_GDAL": "1"}), mock.patch.object(
                publishing_concierge.shutil,
                "which",
                return_value="/usr/bin/ogr2ogr",
            ), mock.patch.object(
                publishing_concierge.subprocess,
                "run",
                return_value=mock.Mock(
                    returncode=0,
                    stdout="source_id,NAME,GIS_AREA_K\nA1,North Reef,10\nA2,North Reef,11\nA3,South Reef,12\n",
                    stderr="",
                ),
            ) as run:
                plan = publishing_concierge.build_plan(
                    source=source,
                    asset_slug="example",
                    title="Example",
                    category="300-infrastructure-industrial",
                    subcategory="330-offshore-platforms",
                    owner="SkyTruth",
                    source_name="Example source",
                    license_text="Example license",
                    citation="Example citation",
                    update_cadence="manual",
                    canonical_format=None,
                    access_tier="public",
                    bucket="example-bucket",
                    release_date=None,
                    categories_path=categories,
                    docs_dir=root / "docs/assets",
                )

        self.assertEqual(run.call_args.args[0][:3], ["ogr2ogr", "-f", "CSV"])
        self.assertNotIn("-limit", run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["timeout"], publishing_concierge.OGR_PROFILE_TIMEOUT_SECONDS)
        self.assertEqual(plan.curator_field_options.profile_scope, "full")
        self.assertTrue(plan.curator_field_options.generated_row_id_option.available)
        self.assertEqual(plan.curator_field_options.id_field_candidates[0].field, "source_id")
        self.assertEqual(plan.curator_field_options.group_field_candidates[0].field, "NAME")
        self.assertTrue(any("Curator must choose grouping fields" in note for note in plan.curator_field_options.notes))

    def test_curator_field_options_do_not_profile_ogr_without_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.fgb"
            source.write_text("placeholder")

            with mock.patch.dict(publishing_concierge.os.environ, {}, clear=True), mock.patch.object(
                publishing_concierge.subprocess,
                "run",
            ) as run:
                plan = publishing_concierge.build_plan(
                    source=source,
                    asset_slug="example",
                    title="Example",
                    category="300-infrastructure-industrial",
                    subcategory="330-offshore-platforms",
                    owner="SkyTruth",
                    source_name="Example source",
                    license_text="Example license",
                    citation="Example citation",
                    update_cadence="manual",
                    canonical_format=None,
                    access_tier="public",
                    bucket="example-bucket",
                    release_date=None,
                    categories_path=categories,
                    docs_dir=root / "docs/assets",
                )

        run.assert_not_called()
        self.assertEqual(plan.curator_field_options.profile_scope, "unavailable")
        self.assertTrue(plan.curator_field_options.generated_row_id_option.available)
        self.assertTrue(any("not profiled with GDAL" in note for note in plan.curator_field_options.notes))

    def test_curator_field_options_skip_large_geojson_feature_collection_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}' + (" " * 32))

            with mock.patch.object(publishing_concierge, "MAX_IN_MEMORY_GEOJSON_BYTES", 16), mock.patch.object(
                publishing_concierge,
                "read_geojson_rows",
            ) as read_rows:
                plan = publishing_concierge.build_plan(
                    source=source,
                    asset_slug="example",
                    title="Example",
                    category="300-infrastructure-industrial",
                    subcategory="330-offshore-platforms",
                    owner="SkyTruth",
                    source_name="Example source",
                    license_text="Example license",
                    citation="Example citation",
                    update_cadence="manual",
                    canonical_format=None,
                    access_tier="public",
                    bucket="example-bucket",
                    release_date=None,
                    categories_path=categories,
                    docs_dir=root / "docs/assets",
                )

        read_rows.assert_not_called()
        self.assertTrue(any("too large for in-memory" in note for note in plan.curator_field_options.notes))

    def test_existing_fgb_still_uses_vector_build_for_pmtiles_companion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.fgb"
            source.write_text("placeholder")

            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        self.assertEqual(plan.available_formats, ["fgb", "pmtiles"])
        self.assertTrue(any("vector_asset.py build" in command for command in plan.suggested_commands))
        self.assertTrue(any("--maxzoom auto" in command for command in plan.suggested_commands))
        self.assertFalse(any(" cp " in command for command in plan.suggested_commands))

    def test_write_draft_doc_refuses_existing_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "docs/assets/example.md"
            path.parent.mkdir(parents=True)
            path.write_text("existing")

            with self.assertRaisesRegex(publishing_concierge.ConciergeError, "refusing to overwrite"):
                publishing_concierge.write_draft_doc(path, "draft", overwrite=False)

    def test_main_requires_workflow_subcommand(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    publishing_concierge.main(
                        [
                            str(source),
                            "--asset-slug",
                            "example-asset",
                            "--category",
                            "100-geographic-reference",
                        ]
                    )

        self.assertEqual(exc.exception.code, 2)

    def test_start_creates_default_state_file_under_temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            self.assertTrue(state_file.exists())
            self.assertTrue(str(state_file).startswith(str(root / "work")))
            state = json.loads(state_file.read_text())
            self.assertEqual(state["schema_version"], publishing_concierge.WORKFLOW_SCHEMA_VERSION)
            self.assertEqual(state["workflow_type"], "first-upload")
            self.assertEqual(state["plan"]["asset_slug"], "example")
            self.assertEqual(state["steps"]["resolve-metadata"]["status"], "pending")

    def test_start_accepts_preview_only_and_uses_preview_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("id,name\n1,A\n")

            stdout = io.StringIO()
            with mock.patch.dict(publishing_concierge.os.environ, {"SHARED_DATASETS_WORKDIR": str(root / "work")}):
                with contextlib.redirect_stdout(stdout):
                    code = publishing_concierge.main(
                        [
                            "start",
                            str(source),
                            "--asset-slug",
                            "example",
                            "--category",
                            "300-infrastructure-industrial",
                            "--subcategory",
                            "330-offshore-platforms",
                            "--request-classification",
                            "preview-only",
                            "--proposal-id",
                            "preview-123",
                            "--release-date",
                            "2026-05-01",
                            "--preview-ref",
                            "feat/test-preview",
                            "--categories",
                            str(categories),
                            "--docs-dir",
                            str(root / "docs/assets"),
                        ]
                    )

            self.assertEqual(code, 0)
            state = json.loads(Path(json.loads(stdout.getvalue())["state_file"]).read_text())
            self.assertEqual(state["request_classification"], "preview-only")
            self.assertEqual(state["preview_ref"], "feat/test-preview")
            self.assertEqual(state["bucket"], publishing_concierge.PREVIEW_BUCKET)
            self.assertTrue(state["plan"]["canonical_path"].startswith(f"gs://{publishing_concierge.PREVIEW_BUCKET}/"))
            self.assertTrue(any("Do not use production publish-release" in command for command in state["generated_commands"]["remote_write"]))
            self.assertTrue(
                any(
                    "production catalog-web-deploy.yml automation runs after reviewed main pushes" in command
                    and "preview-only bucket uploads do not trigger it" in command
                    for command in state["generated_commands"]["remote_write"]
                )
            )
            status = publishing_concierge.render_status(state)
            by_step = {step["step_id"]: step for step in status["steps"]}
            self.assertTrue(by_step["preview-upload"]["required"])
            self.assertTrue(by_step["preview-catalog-refresh"]["required"])
            self.assertTrue(by_step["preview-viewer-verify"]["required"])
            self.assertFalse(by_step["document-asset"]["required"])
            self.assertFalse(by_step["stage-scratch"]["required"])
            self.assertFalse(by_step["pr-ready"]["required"])
            refresh_commands = publishing_concierge.commands_for_preview_catalog_refresh(state)
            self.assertTrue(any("catalog-web-deploy.yml" in command for command in refresh_commands))
            self.assertTrue(any("preview-only GCS uploads do not trigger" in command for command in refresh_commands))

    def test_preview_start_requires_release_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("id,name\n1,A\n")

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    [
                        "start",
                        str(source),
                        "--asset-slug",
                        "example",
                        "--category",
                        "300-infrastructure-industrial",
                        "--subcategory",
                        "330-offshore-platforms",
                        "--request-classification",
                        "preview-only",
                        "--proposal-id",
                        "pr-123",
                        "--categories",
                        str(categories),
                        "--docs-dir",
                        str(root / "docs/assets"),
                    ]
                )

            self.assertEqual(code, 2)

    def test_preview_release_vector_requires_preview_load_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                canonical_format="fgb",
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            state = json.loads(state_file.read_text())
            status = publishing_concierge.render_status(state)
            by_step = {step["step_id"]: step for step in status["steps"]}

            self.assertTrue(by_step["preview-upload"]["required"])
            self.assertTrue(by_step["preview-load"]["required"])
            self.assertTrue(by_step["preview-catalog-refresh"]["required"])
            self.assertTrue(by_step["preview-viewer-verify"]["required"])
            self.assertFalse(by_step["catalog-web"]["required"])

    def test_start_blocks_duplicate_first_upload_asset_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "example.csv"
            source.write_text("id,name\n1,A\n")
            docs_dir = root / "docs/assets"
            docs_dir.mkdir(parents=True)
            (docs_dir / "example.md").write_text("existing")

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    [
                        "start",
                        str(source),
                        "--asset-slug",
                        "example",
                        "--category",
                        "300-infrastructure-industrial",
                        "--subcategory",
                        "330-offshore-platforms",
                        "--request-classification",
                        "canonical-publish",
                        "--proposal-id",
                        "pr-123",
                        "--categories",
                        str(categories),
                        "--docs-dir",
                        str(docs_dir),
                    ]
                )

            self.assertEqual(code, 2)

    def test_next_waits_on_same_step_until_valid_evidence_is_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["next", "--state-file", str(state_file), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["step_id"], "resolve-metadata")

            bad_evidence = self._write_json(root / "bad.json", {"source_name": "Only source"})
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                bad_code = publishing_concierge.main(
                    ["confirm", "--state-file", str(state_file), "--step", "resolve-metadata", "--evidence-json", str(bad_evidence)]
                )
            self.assertEqual(bad_code, 2)

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["next", "--state-file", str(state_file), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["step_id"], "resolve-metadata")

    def test_cannot_confirm_later_step_before_current_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            evidence = self._write_json(root / "contract.json", {})

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    ["confirm", "--state-file", str(state_file), "--step", "settle-contract", "--evidence-json", str(evidence)]
                )

            self.assertEqual(code, 2)
            state = json.loads(state_file.read_text())
            self.assertEqual(state["steps"]["settle-contract"]["status"], "pending")

    def test_generated_id_decisions_require_explicit_profile_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "resolve-metadata",
                    {
                        "source_name": "Example source",
                        "license": "Example license",
                        "citation": "Example citation",
                        "steward": "Data Steward",
                        "source_version_date": "2026-05-01",
                        "update_cadence": "manual",
                        "intended_consumers": ["test"],
                        "shared_datasets_rationale": "Reusable reference table for multiple projects.",
                        "alternatives_considered": "Project storage.",
                        "deprecation_exit_policy": "Deprecate with a successor.",
                        "estimated_published_footprint": "1 MB",
                    },
                ),
                0,
            )
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "settle-contract",
                    {
                        "confirmed_asset_slug": "example",
                        "confirmed_category": "300-infrastructure-industrial",
                        "confirmed_subcategory": "330-offshore-platforms",
                        "confirmed_canonical_format": "csv",
                        "release_layout": "latest-only",
                        "access_tier": "private",
                        "exception_flags": {
                            "public_access_approved": False,
                            "new_top_level_category_approved": False,
                            "new_canonical_format_approved": False,
                            "large_data_exception_approved": False,
                            "incompatible_schema_change_approved": False,
                            "move_or_delete_releases_approved": False,
                            "unsafe_overwrite_approved": False,
                            "infrastructure_mutation_approved": False,
                        },
                    },
                ),
                0,
            )

            code = self._confirm(
                root,
                state_file,
                "profile-fields",
                {
                    "decision_table_present": True,
                    "profile_scope": "full",
                    "provider_id_decision": "use-provider-id",
                    "provider_id_fields": ["source_id"],
                    "generated_group_id_decision": "approved",
                    "group_id_fields": ["NAME"],
                    "generated_row_id_decision": "not-needed",
                    "ext_id_decision": "provider-id",
                    "ext_id_fields": ["source_id"],
                    "search_fields": ["NAME"],
                },
            )

            self.assertEqual(code, 2)

    def test_release_vector_requires_translation_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root, canonical_format="fgb", release_date="2026-05-01")
            state = json.loads(state_file.read_text())
            self.assertTrue(publishing_concierge.translation_decision_required(state))

    def test_large_data_exception_is_required_when_footprint_is_at_least_10gb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "resolve-metadata",
                    {
                        "source_name": "Example source",
                        "license": "Example license",
                        "citation": "Example citation",
                        "steward": "Data Steward",
                        "source_version_date": "2026-05-01",
                        "update_cadence": "manual",
                        "intended_consumers": ["test"],
                        "shared_datasets_rationale": "Reusable reference table for multiple projects.",
                        "alternatives_considered": "Project storage.",
                        "deprecation_exit_policy": "Deprecate with a successor.",
                        "estimated_published_footprint": "12 GB",
                    },
                ),
                0,
            )

            code = self._confirm(
                root,
                state_file,
                "settle-contract",
                {
                    "confirmed_asset_slug": "example",
                    "confirmed_category": "300-infrastructure-industrial",
                    "confirmed_subcategory": "330-offshore-platforms",
                    "confirmed_canonical_format": "csv",
                    "release_layout": "latest-only",
                    "access_tier": "private",
                    "exception_flags": {
                        "public_access_approved": False,
                        "new_top_level_category_approved": False,
                        "new_canonical_format_approved": False,
                        "large_data_exception_approved": False,
                        "incompatible_schema_change_approved": False,
                        "move_or_delete_releases_approved": False,
                        "unsafe_overwrite_approved": False,
                        "infrastructure_mutation_approved": False,
                    },
                },
            )

            self.assertEqual(code, 2)

    def test_artifact_validation_failure_blocks_advancement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            # Move to build-artifacts.
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "resolve-metadata",
                    {
                        "source_name": "Example source",
                        "license": "Example license",
                        "citation": "Example citation",
                        "steward": "Data Steward",
                        "source_version_date": "2026-05-01",
                        "update_cadence": "manual",
                        "intended_consumers": ["test"],
                        "shared_datasets_rationale": "Reusable reference table.",
                        "alternatives_considered": "Project storage.",
                        "deprecation_exit_policy": "Deprecate with a successor.",
                        "estimated_published_footprint": "1 MB",
                    },
                ),
                0,
            )
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "settle-contract",
                    {
                        "confirmed_asset_slug": "example",
                        "confirmed_category": "300-infrastructure-industrial",
                        "confirmed_subcategory": "330-offshore-platforms",
                        "confirmed_canonical_format": "csv",
                        "release_layout": "latest-only",
                        "access_tier": "private",
                        "exception_flags": {
                            "public_access_approved": False,
                            "new_top_level_category_approved": False,
                            "new_canonical_format_approved": False,
                            "large_data_exception_approved": False,
                            "incompatible_schema_change_approved": False,
                            "move_or_delete_releases_approved": False,
                            "unsafe_overwrite_approved": False,
                            "infrastructure_mutation_approved": False,
                        },
                    },
                ),
                0,
            )
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "profile-fields",
                    {
                        "decision_table_present": True,
                        "profile_scope": "full",
                        "provider_id_decision": "use-provider-id",
                        "provider_id_fields": ["source_id"],
                        "generated_group_id_decision": "not-needed",
                        "group_id_fields": [],
                        "generated_row_id_decision": "not-needed",
                        "ext_id_decision": "provider-id",
                        "ext_id_fields": ["source_id"],
                        "search_fields": ["NAME"],
                    },
                ),
                0,
            )

            code = self._confirm(
                root,
                state_file,
                "build-artifacts",
                {"artifacts": [{"path": str(root / "missing.csv"), "format": "csv", "role": "canonical"}]},
            )

            self.assertEqual(code, 2)
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                next_code = publishing_concierge.main(["next", "--state-file", str(state_file), "--json"])
            self.assertEqual(next_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["step_id"], "build-artifacts")

    def test_preview_upload_rejects_production_bucket_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_csv_workflow_through_validate(root, state_file)

            code = self._confirm(
                root,
                state_file,
                "preview-upload",
                {
                    "uploaded_objects": [
                        {
                            "uri": "gs://skytruth-shared-datasets-1/300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/example.csv",
                            "generation": "111",
                            "role": "canonical",
                        }
                    ]
                },
            )

            self.assertEqual(code, 2)

    def test_preview_upload_rejects_role_path_mismatches(self):
        cases = [
            ("release-index", 5, "gs://skytruth-shared-datasets-1-preview/300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/example-release.json"),
            ("run-record", 6, "gs://skytruth-shared-datasets-1-preview/_catalog/releases/example-run.json"),
            ("pmtiles", 1, "gs://skytruth-shared-datasets-1-preview/300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/example-not-pmtiles.json"),
            ("feature-metadata-sidecar", 2, "gs://skytruth-shared-datasets-1-preview/300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/example.metadata.json"),
            ("schema", 3, "gs://skytruth-shared-datasets-1-preview/300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/example.schema.txt"),
        ]
        for _role, object_index, bad_uri in cases:
            with self.subTest(bad_uri=bad_uri), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_file = self._start_workflow(
                    root,
                    canonical_format="fgb",
                    release_date="2026-05-01",
                    request_classification="preview-only",
                )
                self._complete_preview_fgb_workflow_through_validate(root, state_file)
                objects = self._preview_fgb_uploaded_objects()
                objects[object_index] = dict(objects[object_index])
                objects[object_index]["uri"] = bad_uri

                code = self._confirm(root, state_file, "preview-upload", {"uploaded_objects": objects})

                self.assertEqual(code, 2)

    def test_preview_upload_rejects_sidecar_missing_ndjson_cache_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                canonical_format="fgb",
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_fgb_workflow_through_validate(root, state_file)
            objects = self._preview_fgb_uploaded_objects()
            objects[2] = dict(objects[2])
            objects[2].pop("content_type")
            objects[2].pop("cache_control")

            code = self._confirm(root, state_file, "preview-upload", {"uploaded_objects": objects})

            self.assertEqual(code, 2)

    def test_preview_upload_requires_all_autogenerated_localized_sidecars(self):
        no_cache = publishing_concierge.no_cache_control()
        release_prefix = (
            "gs://skytruth-shared-datasets-1-preview/"
            "300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/"
        )
        objects = [
            *self._preview_fgb_uploaded_objects(),
            {
                "uri": f"{release_prefix}example.metadata-translations.csv",
                "generation": "118",
                "role": "metadata-translations",
                "content_type": "text/csv",
                "cache_control": no_cache,
            },
            {
                "uri": f"{release_prefix}example.metadata.es.ndjson.gz",
                "generation": "119",
                "role": "localized-metadata-sidecar",
                "locale": "es",
                "content_type": "application/x-ndjson",
                "cache_control": no_cache,
            },
        ]

        with self.assertRaisesRegex(publishing_concierge.WorkflowError, "localized-metadata-sidecar:fr"):
            publishing_concierge.validate_preview_upload(
                self._autogenerated_localized_state(),
                {"uploaded_objects": objects},
            )

    def test_preview_upload_captures_all_autogenerated_localized_sidecars(self):
        no_cache = publishing_concierge.no_cache_control()
        release_prefix = (
            "gs://skytruth-shared-datasets-1-preview/"
            "300-infrastructure-industrial/330-offshore-platforms/example/releases/2026-05-01/"
        )
        objects = [
            *self._preview_fgb_uploaded_objects(),
            {
                "uri": f"{release_prefix}example.metadata-translations.csv",
                "generation": "118",
                "role": "metadata-translations",
                "content_type": "text/csv",
                "cache_control": no_cache,
            },
            {
                "uri": f"{release_prefix}example.metadata.es.ndjson.gz",
                "generation": "119",
                "role": "localized-metadata-sidecar",
                "locale": "es",
                "content_type": "application/x-ndjson",
                "cache_control": no_cache,
            },
            {
                "uri": f"{release_prefix}example.metadata.fr.ndjson.gz",
                "generation": "120",
                "role": "localized-metadata-sidecar:fr",
                "content_type": "application/x-ndjson",
                "cache_control": no_cache,
            },
        ]

        normalized = publishing_concierge.validate_preview_upload(
            self._autogenerated_localized_state(),
            {"uploaded_objects": objects},
        )

        self.assertIn("metadata-translations", [obj["role"] for obj in normalized["uploaded_objects"]])
        self.assertEqual(
            sorted(obj["role"] for obj in normalized["uploaded_objects"] if obj["role"].startswith("localized-metadata-sidecar:")),
            ["localized-metadata-sidecar:es", "localized-metadata-sidecar:fr"],
        )

    def test_preview_upload_rejects_schema_missing_json_cache_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                canonical_format="fgb",
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_fgb_workflow_through_validate(root, state_file)
            objects = self._preview_fgb_uploaded_objects()
            objects[3] = dict(objects[3])
            objects[3].pop("content_type")
            objects[3].pop("cache_control")

            code = self._confirm(root, state_file, "preview-upload", {"uploaded_objects": objects})

            self.assertEqual(code, 2)

    def test_fgb_preview_validation_rejects_missing_gdal_even_with_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                canonical_format="fgb",
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_fgb_workflow_through_build(root, state_file)

            code = self._confirm(root, state_file, "validate-artifacts", self._fgb_validation_payload(include_gdal=False))

            self.assertEqual(code, 2)

    def test_fgb_preview_validation_accepts_gdal_and_pmtiles_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                canonical_format="fgb",
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_fgb_workflow_through_build(root, state_file)

            code = self._confirm(root, state_file, "validate-artifacts", self._fgb_validation_payload())

            self.assertEqual(code, 0)
            state = json.loads(state_file.read_text())
            self.assertEqual(state["steps"]["validate-artifacts"]["evidence"]["gdal"]["ogrinfo"], "/usr/bin/ogrinfo GDAL 3.8.0")

    def test_preview_load_rejects_inputs_that_do_not_match_upload_evidence(self):
        cases = [
            ("sidecar_uri", "gs://skytruth-shared-datasets-1-preview/other/example.metadata.ndjson.gz"),
            ("sidecar_generation", "999"),
        ]
        for key, bad_value in cases:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_file = self._start_workflow(
                    root,
                    canonical_format="fgb",
                    release_date="2026-05-01",
                    request_classification="preview-only",
                )
                self._complete_preview_fgb_workflow_through_upload(root, state_file)
                payload = self._preview_load_payload()
                payload["inputs"][key] = bad_value

                code = self._confirm(root, state_file, "preview-load", payload)

                self.assertEqual(code, 2)

    def test_preview_load_rejects_weak_dispatch_evidence(self):
        cases = [
            ("dispatched_ref", "main", "set"),
            ("workflow_inputs_checked_against_preview_ref", False, "set"),
            ("workflow_inputs_checked_against_preview_ref", None, "pop"),
            ("status", "failed", "set"),
            ("status", "", "set"),
        ]
        for key, bad_value, action in cases:
            with self.subTest(key=key, bad_value=bad_value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                state_file = self._start_workflow(
                    root,
                    canonical_format="fgb",
                    release_date="2026-05-01",
                    request_classification="preview-only",
                )
                self._complete_preview_fgb_workflow_through_upload(root, state_file)
                payload = self._preview_load_payload()
                if action == "pop":
                    payload.pop(key)
                else:
                    payload[key] = bad_value

                code = self._confirm(root, state_file, "preview-load", payload)

                self.assertEqual(code, 2)

    def test_preview_fgb_workflow_completes_with_cross_checked_load_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                canonical_format="fgb",
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_fgb_workflow_through_upload(root, state_file)

            self.assertEqual(self._confirm(root, state_file, "preview-load", self._preview_load_payload()), 0)
            refresh = self._preview_catalog_refresh_payload()
            self.assertEqual(self._confirm(root, state_file, "preview-catalog-refresh", refresh), 0)
            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "preview-viewer-verify",
                    self._preview_catalog_asset_payload(self._preview_fgb_uploaded_objects()),
                ),
                0,
            )

            report_stdout = io.StringIO()
            with contextlib.redirect_stdout(report_stdout):
                report_code = publishing_concierge.main(["render-report", "--state-file", str(state_file)])
            self.assertEqual(report_code, 0)
            report = report_stdout.getvalue()
            self.assertIn("GDAL/OGR: ogr2ogr=/usr/bin/ogr2ogr GDAL 3.8.0", report)
            self.assertIn("Workflow inputs checked against preview ref: True", report)
            self.assertIn("generation 113, role feature-metadata-sidecar", report)
            self.assertIn("Preview data mode: preserve", report)
            self.assertIn("Verified uploaded release artifact URIs: 5", report)

    def test_preview_viewer_verify_rejects_catalog_missing_uploaded_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_csv_workflow_through_validate(root, state_file)
            uploaded_objects = self._preview_csv_uploaded_objects()
            self.assertEqual(self._confirm(root, state_file, "preview-upload", {"uploaded_objects": uploaded_objects}), 0)
            self.assertEqual(self._confirm(root, state_file, "preview-catalog-refresh", self._preview_catalog_refresh_payload()), 0)
            payload = self._preview_catalog_asset_payload(uploaded_objects)
            payload["catalog_asset"]["versions"][0]["files"] = []

            code = self._confirm(root, state_file, "preview-viewer-verify", payload)

            self.assertEqual(code, 2)

    def test_preview_csv_workflow_completes_without_pr_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(
                root,
                release_date="2026-05-01",
                request_classification="preview-only",
            )
            self._complete_preview_csv_workflow_through_validate(root, state_file)
            uploaded_objects = self._preview_csv_uploaded_objects()

            self.assertEqual(
                self._confirm(
                    root,
                    state_file,
                    "preview-upload",
                    {"uploaded_objects": uploaded_objects},
                ),
                0,
            )
            self.assertEqual(self._confirm(root, state_file, "preview-catalog-refresh", self._preview_catalog_refresh_payload()), 0)
            self.assertEqual(
                self._confirm(root, state_file, "preview-viewer-verify", self._preview_catalog_asset_payload(uploaded_objects)),
                0,
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["validate", "--state-file", str(state_file)])
            self.assertEqual(code, 0)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ready_for_preview"])
            self.assertFalse(result["ready_for_pr"])

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                render_pr_code = publishing_concierge.main(["render-pr", "--state-file", str(state_file)])
            self.assertEqual(render_pr_code, 2)

            report_stdout = io.StringIO()
            with contextlib.redirect_stdout(report_stdout):
                report_code = publishing_concierge.main(["render-report", "--state-file", str(state_file)])
            self.assertEqual(report_code, 0)
            self.assertIn("Request classification: `preview-only`", report_stdout.getvalue())
            self.assertIn("generation 111, role canonical", report_stdout.getvalue())

    def test_render_pr_uses_reviewed_publish_plan_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self._complete_first_csv_workflow_through_pr_ready(root, state_file)

            self.assertEqual(
                self._confirm(root, state_file, "pr-ready", {"reviewed_pr_body": True}),
                0,
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["render-pr", "--state-file", str(state_file)])

            self.assertEqual(code, 0)
            body = stdout.getvalue()
            self.assertIn("```shared-datasets-publish-plan", body)
            self.assertIn("_catalog/web/catalog.json", body)

    def test_render_report_outputs_completion_scaffold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)
            self._complete_first_csv_workflow_through_pr_ready(root, state_file)
            self.assertEqual(
                self._confirm(root, state_file, "pr-ready", {"reviewed_pr_body": True}),
                0,
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = publishing_concierge.main(["render-report", "--state-file", str(state_file)])

            self.assertEqual(code, 0)
            report = stdout.getvalue()
            self.assertIn("## Completion Report", report)
            self.assertIn("## Remote Paths", report)

    def test_yes_cannot_skip_evidence_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_file = self._start_workflow(root)

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = publishing_concierge.main(
                    ["confirm", "--state-file", str(state_file), "--step", "resolve-metadata", "--yes"]
                )

            self.assertEqual(code, 2)

    def test_draft_asset_doc_contains_access_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")
            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date="2026-05-01",
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        text = publishing_concierge.draft_asset_doc(
            plan,
            owner="SkyTruth",
            source_name="Example source",
            license_text="Example license",
            citation="Example citation",
            update_cadence="manual",
            access_tier="public",
        )
        self.assertIn("access_tier: public", text)
        self.assertIn("citation: Example citation", text)
        self.assertIn("latest/example-asset.pmtiles", text)

    def test_pmtiles_hints_are_included_in_vector_command_and_draft_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            categories = root / "categories.yaml"
            categories.write_text(CATEGORIES_YAML)
            source = root / "source.shp"
            source.write_text("placeholder")
            plan = publishing_concierge.build_plan(
                source=source,
                asset_slug="example-asset",
                title="Example Asset",
                category="100-geographic-reference",
                subcategory="110-boundaries",
                owner="SkyTruth",
                source_name="Example source",
                license_text="Example license",
                citation="Example citation",
                update_cadence="manual",
                canonical_format=None,
                access_tier="public",
                bucket="example-bucket",
                release_date=None,
                source_scale_denominator=10_000_000,
                pmtiles_detail_hint="medium",
                categories_path=categories,
                docs_dir=root / "docs/assets",
            )

        command = next(command for command in plan.suggested_commands if "vector_asset.py build" in command)
        self.assertIn("--source-scale-denominator 10000000", command)
        self.assertIn("--pmtiles-detail-hint medium", command)
        self.assertTrue(any("source/detail hints" in note for note in plan.notes))

        text = publishing_concierge.draft_asset_doc(
            plan,
            owner="SkyTruth",
            source_name="Example source",
            license_text="Example license",
            citation="Example citation",
            update_cadence="manual",
            access_tier="public",
        )
        self.assertIn("source_scale_denominator: 10000000", text)
        self.assertIn("pmtiles_detail_hint: medium", text)


if __name__ == "__main__":
    unittest.main()
