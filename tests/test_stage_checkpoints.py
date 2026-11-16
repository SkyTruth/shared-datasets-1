"""Persisted stage boundaries, interruption recovery and fail-fast controls."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from ingestion.common import stage_checkpoints as checkpoints, publication_sources
from scripts import publication_checkpoint, feature_metadata_translation_reuse as reuse
from test_publication_readiness import config_for_file
from test_feature_metadata_translation_reuse import record, row, source_bundle

CODE = ("ingestion/common/feature_metadata.py",)


@pytest.fixture
def native_suite(tmp_path, monkeypatch):
    specifications = {
        "gdalinfo": ("--version", "GDAL 3.6.2"),
        "ogr2ogr": ("--version", "GDAL 3.6.2"),
        "tippecanoe": ("--version", "tippecanoe v2.52.0"),
        "pmtiles": ("version", "pmtiles 1.30.1"),
    }
    directory = tmp_path / "bin"
    directory.mkdir()
    for name in (*specifications, "gdal_calc.py", "gdal_polygonize.py", "tippecanoe-decode"):
        path = directory / name
        body = "exit 99\n"
        if name in specifications:
            argument, version = specifications[name]
            redirect = " >&2" if name == "tippecanoe" else ""
            body = f'[ "$#" = 1 ] && [ "$1" = "{argument}" ] || exit 98\necho "{version}"{redirect}\n'
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(directory))
    return directory


@pytest.mark.parametrize("names", [(), ("gdal_calc.py",),
    ("gdalinfo", "gdal_calc.py", "gdal_polygonize.py", "ogr2ogr", "tippecanoe", "tippecanoe-decode", "pmtiles")])
def test_native_suite_identity_survives_checkpoint_serialization(tmp_path, native_suite, names):
    tools = checkpoints.native_tools(names)
    versions = {"gdalinfo": "GDAL 3.6.2", "gdal_calc.py": "GDAL 3.6.2",
        "gdal_polygonize.py": "GDAL 3.6.2", "ogr2ogr": "GDAL 3.6.2",
        "tippecanoe": "tippecanoe v2.52.0", "tippecanoe-decode": "tippecanoe v2.52.0",
        "pmtiles": "pmtiles 1.30.1"}
    expected = {name: {"path": str(native_suite / name), "version": versions[name],
        "sha256": hashlib.sha256((native_suite / name).read_bytes()).hexdigest()} for name in names}
    assert tools == expected
    store = checkpoints.StageStore(tmp_path / "cache")
    path, _ = store.run("native-build", {"tools": tools}, output, code=CODE)
    assert checkpoints.verify(path / "checkpoint.json")["binding"]["inputs"]["tools"] == expected
    resumed, _ = store.run("native-build", {"tools": checkpoints.native_tools(names)},
        lambda _: pytest.fail("unchanged tool identities must reuse the stage"), code=CODE)
    assert resumed == path


@pytest.mark.parametrize("failure", ["missing-tool", "missing-reporter", "failed-version", "empty-version"])
def test_invalid_native_identity_fails_before_checkpoint_producer(tmp_path, native_suite, failure):
    if failure == "missing-tool":
        (native_suite / "gdal_calc.py").unlink()
    elif failure == "missing-reporter":
        (native_suite / "gdalinfo").unlink()
    else:
        (native_suite / "gdalinfo").write_text("#!/bin/sh\n" + (
            "echo 'broken version probe'\nexit 1\n" if failure == "failed-version" else "exit 0\n"))
    with pytest.raises((RuntimeError, OSError, subprocess.CalledProcessError)):
        checkpoints.StageStore(tmp_path / "cache").run("native-build",
            {"tools": checkpoints.native_tools(("gdal_calc.py",))},
            lambda _: pytest.fail("invalid tool identity cannot reach the producer"), code=CODE)
    assert not (tmp_path / "cache").exists()


def test_native_version_timeout_remains_a_failure(native_suite, monkeypatch):
    def timeout(command, **kwargs):
        assert command == [str(native_suite / "gdalinfo"), "--version"]
        assert kwargs["timeout"] == 15
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(checkpoints.subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        checkpoints.native_tools(("gdal_calc.py",))


def test_native_script_hash_and_suite_version_invalidate_completed_stage(tmp_path, native_suite):
    store = checkpoints.StageStore(tmp_path / "cache")

    def build():
        return store.run("native-build", {"tools": checkpoints.native_tools(("gdal_calc.py",))},
            output, code=CODE)[0]

    first = build()
    script = native_suite / "gdal_calc.py"
    script.write_text(script.read_text() + "# updated utility implementation\n")
    changed_script = build()
    assert changed_script != first
    reporter = native_suite / "gdalinfo"
    reporter.write_text(reporter.read_text().replace("3.6.2", "3.6.3"))
    assert build() not in {first, changed_script}


def test_python_utilities_resolve_suite_reporters_from_a_separate_path_entry(tmp_path, native_suite, monkeypatch):
    scripts = tmp_path / "venv-bin"
    scripts.mkdir()
    for name in ("gdal_calc.py", "gdal_polygonize.py", "tippecanoe-decode"):
        (native_suite / name).rename(scripts / name)
    monkeypatch.setenv("PATH", str(scripts) + ":" + str(native_suite))
    tools = checkpoints.native_tools(("gdal_calc.py", "gdal_polygonize.py", "tippecanoe-decode"))
    for name in ("gdal_calc.py", "gdal_polygonize.py", "tippecanoe-decode"):
        assert tools[name]["path"] == str(scripts / name)
        assert tools[name]["version"] == ("tippecanoe v2.52.0" if name == "tippecanoe-decode" else "GDAL 3.6.2")


def output(directory):
    (directory / "data.csv").write_text("id\n1\n")
    return {"rows": 1}


def test_new_process_reuses_complete_stage_after_later_failure(tmp_path):
    store = checkpoints.StageStore(tmp_path / "cache")
    first, _ = store.run("normalize", {"release": "2026-11-01"}, output, code=CODE)

    def fail(directory):
        (directory / "partial").write_text("incomplete")
        raise RuntimeError("late stage failed")

    with pytest.raises(RuntimeError, match="late stage"):
        store.run("metadata", {}, fail, code=CODE)
    assert not list((tmp_path / "cache/metadata").glob("*/checkpoint.json"))
    resumed, value = checkpoints.StageStore(tmp_path / "cache").run(
        "normalize",
        {"release": "2026-11-01"},
        lambda _: pytest.fail("completed stage must not run again"),
        code=CODE,
    )
    assert resumed == first and value == {"rows": 1}


def test_only_affected_input_and_stage_code_changes_invalidate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "ingestion/common").mkdir(parents=True)
    (repo / "ingestion/common/stage_checkpoints.py").write_text("cache-v1")
    producer = repo / "producer.py"
    producer.write_text("producer-v1")
    monkeypatch.setattr(checkpoints, "REPO", repo)
    source = tmp_path / "source"
    source.write_text("A")
    store = checkpoints.StageStore(tmp_path / "cache")

    def run():
        return store.run(
            "metadata", {}, output, code=("producer.py",), files={"source": source}
        )[0]

    first = run()
    (repo / "unrelated.py").write_text("unrelated repair")
    assert run() == first
    source.write_text("B")
    changed_input = run()
    assert changed_input != first
    producer.write_text("producer-v2")
    assert run() not in {first, changed_input}


@pytest.mark.parametrize("damage", ["content", "extra", "missing"])
def test_corrupt_checkpoint_never_silently_rebuilds(tmp_path, damage):
    store = checkpoints.StageStore(tmp_path)
    directory, _ = store.run("metadata", {}, output, code=CODE)
    if damage == "content":
        (directory / "data.csv").write_text("wrong")
    elif damage == "extra":
        (directory / "unexpected").write_text("wrong")
    else:
        (directory / "data.csv").unlink()
    with pytest.raises(ValueError, match="checksum or size"):
        store.run(
            "metadata",
            {},
            lambda _: pytest.fail("must reject corrupt evidence"),
            code=CODE,
        )


def test_modified_result_payload_rejected_before_producer(tmp_path):
    store = checkpoints.StageStore(tmp_path)
    directory, _ = store.run("metadata", {}, output, code=CODE)
    manifest = directory / "checkpoint.json"
    value = json.loads(manifest.read_text())
    value["payload"]["rows"] = 999
    manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="manifest checksum"):
        store.run(
            "metadata",
            {},
            lambda _: pytest.fail("must reject changed results"),
            code=CODE,
        )


def test_changed_input_during_stage_cannot_commit(tmp_path):
    source = tmp_path / "source"
    source.write_text("A")

    def mutate(directory):
        source.write_text("B")
        return output(directory)

    with pytest.raises(ValueError, match="input changed"):
        checkpoints.StageStore(tmp_path / "cache").run(
            "metadata", {}, mutate, code=CODE, files={"source": source}
        )
    assert not list((tmp_path / "cache").glob("*/*/checkpoint.json"))


def test_pinned_export_restore_and_generation_rejection(tmp_path):
    store = checkpoints.StageStore(tmp_path / "cache")
    store.run("metadata", {"release": "r1"}, output, code=CODE)
    objects = {}

    def upload(name, path):
        data = path.read_bytes()
        objects[name] = data
        return {
            "uri": name,
            "generation": 7,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    reference = store.export(upload)
    resumed = checkpoints.StageStore(tmp_path / "restored")
    resumed.restore(
        reference,
        lambda ref, path: path.write_bytes(objects[ref["uri"]]),
        validate_object=lambda _: None,
    )
    resumed.run(
        "metadata",
        {"release": "r1"},
        lambda _: pytest.fail("restored bytes must be reused"),
        code=CODE,
    )
    list(reference["stages"][0]["files"].values())[-1]["generation"] = 0
    with pytest.raises(ValueError, match="exact object generations"):
        checkpoints.StageStore(tmp_path / "bad").restore(
            reference,
            lambda *_: pytest.fail("invalid references must fail before download"),
            validate_object=lambda _: None,
        )


def test_active_stage_fails_fast_without_running_second_producer(tmp_path):
    store = checkpoints.StageStore(tmp_path)

    def produce(directory):
        with pytest.raises(RuntimeError, match="active producer"):
            store.run(
                "metadata", {}, lambda _: pytest.fail("busy is not success"), code=CODE
            )
        return output(directory)

    store.run("metadata", {}, produce, code=CODE)


def test_cached_build_preserves_arbitrary_source_mapping_and_paths(tmp_path):
    @dataclass
    class Result:
        data: Path
        properties: dict
        manifest: Path

    def produce(directory):
        output(directory)
        return Result(
            directory / "data.csv",
            {"$path": "legitimate source property"},
            directory / "manifest.json",
        )

    first = checkpoints.cached_build(
        "example", {}, tmp_path / "first", produce, result_type=Result, code=CODE
    )
    second = checkpoints.cached_build(
        "example",
        {},
        tmp_path / "second",
        lambda _: pytest.fail("must reuse"),
        result_type=Result,
        code=CODE,
    )
    assert (
        first.properties == second.properties == {"$path": "legitimate source property"}
    )
    assert first.data.read_bytes() == second.data.read_bytes()
    second.data.write_text("human edit")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        checkpoints.cached_build(
            "example", {}, tmp_path / "second", produce, result_type=Result, code=CODE
        )


def build_plan(tmp_path):
    config = config_for_file(tmp_path)
    assert publication_sources.run(config)["state"] == "READY"
    source = config["assets"][0]
    stage = {
        "name": "metadata",
        "inputs": {
            "source": {"path": source["source"], "sha256": source["source_sha256"]}
        },
        "code": list(CODE),
        "outputs": ["data.csv"],
        "command": [
            sys.executable,
            "-c",
            "from pathlib import Path; import shutil, sys; shutil.copyfile(sys.argv[1], Path(sys.argv[2]) / 'data.csv')",
            "{input:source}",
            "{output_dir}",
        ],
        "checks": [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import sys; assert Path(sys.argv[1]).joinpath('data.csv').read_text().startswith('key,name')",
                "{output_dir}",
            ]
        ],
    }
    return {
        "schema_version": 1,
        "asset_slug": "table-example",
        "release": config["release"],
        "source_sha256": config["assets"][0]["source_sha256"],
        "readiness_report": str(Path(config["work_dir"]) / "readiness.json"),
        "stages": [stage],
    }


@pytest.mark.parametrize(
    "failure",
    ["executable", "dependency", "input", "code", "readiness", "unbound-source"],
)
def test_entire_plan_rejected_before_first_expensive_stage(
    tmp_path, monkeypatch, failure
):
    plan = build_plan(tmp_path)
    second = {**plan["stages"][0], "name": "tiles"}
    plan["stages"].append(second)
    if failure == "executable":
        second["command"] = ["missing-publication-test-binary"]
    elif failure == "dependency":
        second["inputs"] = {"source": {"stage": "unknown", "output": "data.csv"}}
    elif failure == "input":
        second["inputs"] = {
            "source": {"path": str(tmp_path / "missing"), "sha256": "a" * 64}
        }
    elif failure == "code":
        second["code"] = ["missing-producer.py"]
    elif failure == "unbound-source":
        wrong = tmp_path / "wrong.csv"
        wrong.write_text("key,name\n1,other data\n")
        for stage in plan["stages"]:
            stage["inputs"] = {
                "source": {
                    "path": str(wrong),
                    "sha256": publication_sources.input_digest(wrong),
                }
            }
    else:
        path = Path(plan["readiness_report"])
        value = json.loads(path.read_text())
        value["state"] = "NEEDS_DECISION"
        path.write_text(json.dumps(value))
    monkeypatch.setattr(
        publication_checkpoint.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("no builder may run"),
    )
    with pytest.raises((ValueError, OSError)):
        publication_checkpoint.run(plan)


def test_real_cli_reuses_stage_in_new_process(tmp_path):
    plan = tmp_path / "stage-plan.json"
    plan.write_text(json.dumps(build_plan(tmp_path)))
    command = [sys.executable, "scripts/publication_checkpoint.py", "--plan", str(plan)]
    first = subprocess.run(command, text=True, capture_output=True)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (
        subprocess.run(command, text=True, capture_output=True).stdout == first.stdout
    )


def test_translation_batch_bad_header_fails_before_index_construction(
    tmp_path, monkeypatch
):
    first = source_bundle(
        tmp_path / "first", [record("1", "a", "A")], [row("1", "A", "FR A")]
    )
    second = source_bundle(
        tmp_path / "second",
        [record("1", "b", "B", slug="other")],
        [row("1", "B", "FR B")],
    )
    Path(second["translation_source"]).write_text("obviously,wrong,header\n")
    monkeypatch.setattr(
        reuse,
        "_build_memory",
        lambda **_: pytest.fail("must inspect both bundles before indexing"),
    )
    with pytest.raises(ValueError, match="preflight failed"):
        reuse.index_checkpoint(
            sources=[first, second],
            fields=["name"],
            locales=["fr"],
            source_key_fields=["SITE_PID"],
        )


@pytest.mark.parametrize("format_name", ["csv", "fgb", "cog", "zarr"])
def test_manual_artifact_checkpoint_restores_each_format(tmp_path, format_name):
    source = tmp_path / ("asset." + format_name)
    if format_name == "zarr":
        source.mkdir()
        (source / "zarr.json").write_text("{}")
    else:
        source.write_bytes(b"synthetic format-neutral stage bytes")
    artifacts = [{"path": str(source), "format": format_name, "role": "canonical"}]
    context = {"source_sha256": "a" * 64, "release": "2026-11-01"}
    manifest = checkpoints.artifact_checkpoint(artifacts, context)
    if source.is_dir():
        shutil.rmtree(source)
    else:
        source.unlink()
    checkpoints.artifact_checkpoint(artifacts, context, manifest=manifest)
    assert source.exists()
    with pytest.raises(ValueError, match="current publication inputs"):
        checkpoints.artifact_checkpoint(
            artifacts, {**context, "release": "2026-12-01"}, manifest=manifest
        )
