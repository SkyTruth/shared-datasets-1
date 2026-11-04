"""Immutable local build checkpoints. These bytes never confer publish authority."""

from contextlib import contextmanager
from dataclasses import fields, is_dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile

from ingestion.common.runtime import sha256_file
from scripts import release_feature_model as model

SCHEMA = "publication-stage-v1"
MARKERS = ("checkpoint.json", "checkpoint.sha256")
LOGGER = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]


def digest(value):
    return hashlib.sha256(model.canonical_json(value).encode()).hexdigest()


def checkpoint_root():
    if os.environ.get("SHARED_DATASETS_CHECKPOINT_DIR"):
        return Path(os.environ["SHARED_DATASETS_CHECKPOINT_DIR"]).resolve()
    root = Path(
        os.environ.get(
            "SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1"
        )
    )
    return root / "_scratch/publication-checkpoints"


def implementation(paths):
    return {
        name: sha256_file(member(REPO, name))
        for name in sorted({"ingestion/common/stage_checkpoints.py", *paths})
    }


def native_tools(names):
    result = {}
    for name in names:
        path = shutil.which(name)
        if path is None:
            result[name] = (
                None  # The producer still fails if its native tool is missing.
            )
        else:
            completed = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=15
            )
            result[name] = {
                "path": str(Path(path).resolve()),
                "version": (completed.stdout + completed.stderr).strip(),
                "sha256": sha256_file(Path(path)),
            }
    return result


def member(directory, name):
    path = directory / name
    if (
        not name
        or Path(name).is_absolute()
        or ".." in Path(name).parts
        or path.is_symlink()
    ):
        raise ValueError("unsafe checkpoint member: " + name)
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("checkpoint member escapes its directory")
    return path


def file_table(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("checkpoint files must not contain symlinks")
        if path.is_file() and path not in {directory / marker for marker in MARKERS}:
            result[path.relative_to(directory).as_posix()] = {
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    return result


def verify(path, binding=None):
    if (
        path.is_symlink()
        or sha256_file(path)
        != member(path.parent, "checkpoint.sha256").read_text().strip()
    ):
        raise ValueError("checkpoint completion manifest checksum changed")
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "binding", "files", "payload"}
        or value["schema"] != SCHEMA
        or (binding is not None and value["binding"] != binding)
    ):
        raise ValueError("checkpoint binding changed")
    if not value.get("files"):
        raise ValueError("checkpoint must contain complete output files")
    for name in value["files"]:
        member(path.parent, name)
    if file_table(path.parent) != value["files"]:
        raise ValueError("checkpoint file set, checksum or size changed")
    return value


@contextmanager
def exclusive(path):
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "checkpoint stage already has an active producer: " + str(path)
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class StageStore:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else checkpoint_root()

    def run(self, stage, inputs, produce, *, code, files=None, expected_files=None):
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", stage):
            raise ValueError("invalid checkpoint stage")
        from ingestion.common.publication_sources import input_digest

        files = files or {}
        code_identity = implementation(code)
        inputs = json.loads(model.canonical_json(inputs))
        for path in files.values():
            if not Path(path).exists():
                raise ValueError("missing checkpoint input: " + str(path))
        pins = {name: input_digest(Path(path)) for name, path in files.items()}
        if any(
            pins.get(name) != expected
            for name, expected in (expected_files or {}).items()
        ):
            raise ValueError("checkpoint input differs from its declared pin")
        binding = {
            "stage": stage,
            "inputs": inputs,
            "files": pins,
            "implementation": code_identity,
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
        }
        parent = self.root / stage
        parent.mkdir(parents=True, exist_ok=True)
        key = digest(binding)
        target = parent / key
        with exclusive(parent / (key + ".lock")):
            reused = target.exists()
            if reused:
                value = verify(target / "checkpoint.json", binding)
            else:
                with tempfile.TemporaryDirectory(
                    prefix="preparing-", dir=parent
                ) as temp:
                    candidate = Path(temp) / "complete"
                    candidate.mkdir()
                    payload = produce(candidate)
                    outputs = file_table(candidate)
                    if not outputs:
                        raise ValueError("checkpoint producer emitted no output files")
                    if {
                        name: input_digest(Path(path)) for name, path in files.items()
                    } != pins:
                        raise ValueError("checkpoint input changed during production")
                    if implementation(code) != binding["implementation"]:
                        raise ValueError(
                            "checkpoint implementation changed during production"
                        )
                    value = {
                        "schema": SCHEMA,
                        "binding": binding,
                        "files": outputs,
                        "payload": payload,
                    }
                    path = candidate / "checkpoint.json"
                    with path.open("x") as stream:
                        stream.write(model.canonical_json(value) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    (candidate / "checkpoint.sha256").write_text(
                        sha256_file(path) + "\n"
                    )
                    value = json.loads(path.read_text())
                    candidate.rename(target)
        LOGGER.info(
            "publication_stage_checkpoint %s",
            model.canonical_json(
                {
                    "stage": stage,
                    "key": key,
                    "reused": reused,
                    "manifest": str(target / "checkpoint.json"),
                }
            ),
        )
        return target, value["payload"]

    def export(self, upload):
        stages = []
        for path in sorted(self.root.glob("*/*/checkpoint.json")):
            value = verify(path)
            files = {}
            for name in (*value["files"], *MARKERS):
                key = digest([value["binding"], name])
                files[name] = upload(
                    key + "-" + Path(name).name, member(path.parent, name)
                )
            stages.append({"binding": value["binding"], "files": files})
        return {"schema": SCHEMA, "stages": stages}

    def validate_reference(self, reference, validate_object):
        if reference.get("schema") != SCHEMA:
            raise ValueError("unsupported checkpoint reference")
        seen = set()
        for item in reference["stages"]:
            binding = item["binding"]
            key = digest(binding)
            if key in seen:
                raise ValueError("duplicate retained checkpoint stage")
            seen.add(key)
            if implementation(binding["implementation"]) != binding["implementation"]:
                raise ValueError("restored checkpoint implementation changed")
            if (
                binding["python"] != platform.python_version()
                or binding["sqlite"] != sqlite3.sqlite_version
            ):
                raise ValueError("restored checkpoint runtime changed")
            stage = binding["stage"]
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", stage):
                raise ValueError("invalid restored checkpoint stage")
            if set(MARKERS) - item["files"].keys():
                raise ValueError(
                    "checkpoint reference is missing its completion manifest"
                )
            for name, ref in item["files"].items():
                member(self.root, name)
                if type(ref.get("generation")) is not int or ref["generation"] <= 0:
                    raise ValueError(
                        "checkpoint restore requires exact object generations"
                    )
                if (
                    not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
                    or type(ref["size"]) is not int
                    or ref["size"] < 0
                ):
                    raise ValueError(
                        "checkpoint restore requires checksum and size pins"
                    )
                validate_object(ref)

    def restore(self, reference, download, *, validate_object):
        self.validate_reference(reference, validate_object)
        for item in reference["stages"]:
            binding = item["binding"]
            stage = binding["stage"]
            parent = self.root / stage
            parent.mkdir(parents=True, exist_ok=True)
            target = parent / digest(binding)
            with exclusive(parent / (digest(binding) + ".lock")):
                if target.exists():
                    verify(target / "checkpoint.json", binding)
                    if (
                        sha256_file(target / "checkpoint.json")
                        != item["files"]["checkpoint.json"]["sha256"]
                    ):
                        raise ValueError(
                            "existing checkpoint differs from the retained manifest"
                        )
                    continue
                with tempfile.TemporaryDirectory(
                    prefix="restoring-", dir=parent
                ) as temp:
                    candidate = Path(temp) / "complete"
                    candidate.mkdir()
                    for name, ref in item["files"].items():
                        path = member(candidate, name)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        download(ref, path)
                        if (
                            sha256_file(path) != ref["sha256"]
                            or path.stat().st_size != ref["size"]
                        ):
                            raise ValueError(
                                "downloaded checkpoint checksum or size changed"
                            )
                    verify(candidate / "checkpoint.json", binding)
                    candidate.rename(target)


def encode_result(value, work, files, *, exclude=(), pending=False):
    if isinstance(value, Path):
        relative = value.resolve().relative_to(work.resolve()).as_posix()
        if value.exists():
            files.add(relative)
        elif not pending:
            raise ValueError("build output is missing: " + str(value))
        return {"$path": relative}
    if isinstance(value, model.GeneratedIdentitySnapshot):
        return {
            "$snapshot": {
                field.name: getattr(value, field.name) for field in fields(value)
            }
        }
    if is_dataclass(value):
        return {
            "$record": {
                field.name: encode_result(
                    getattr(value, field.name),
                    work,
                    files,
                    pending=field.name == "manifest",
                )
                for field in fields(value)
                if field.name not in exclude
            }
        }
    if isinstance(value, tuple):
        return {"$tuple": [encode_result(v, work, files) for v in value]}
    if isinstance(value, list):
        return [encode_result(v, work, files) for v in value]
    if isinstance(value, dict):
        return {"$map": {k: encode_result(v, work, files) for k, v in value.items()}}
    if value is None or type(value) in {str, int, float, bool}:
        return value
    raise TypeError("unsupported checkpoint result type: " + type(value).__name__)


def decode_result(value, work, result_type, extra):
    if isinstance(value, dict):
        if set(value) == {"$path"}:
            return member(work, value["$path"])
        if set(value) == {"$snapshot"}:
            return model.GeneratedIdentitySnapshot(**value["$snapshot"])
        if set(value) == {"$tuple"}:
            return tuple(
                decode_result(v, work, result_type, extra) for v in value["$tuple"]
            )
        if set(value) == {"$record"}:
            return result_type(
                **{
                    k: decode_result(v, work, result_type, {})
                    for k, v in value["$record"].items()
                },
                **extra,
            )
        if set(value) == {"$map"}:
            return {
                k: decode_result(v, work, result_type, extra)
                for k, v in value["$map"].items()
            }
        raise ValueError("invalid checkpoint result encoding")
    if isinstance(value, list):
        return [decode_result(v, work, result_type, extra) for v in value]
    return value


def cached_build(
    stage, inputs, workdir, produce, *, result_type, code, extra=None, files=None
):
    """Cache a pure local builder; restore outputs into a fresh caller workspace."""
    extra = extra or {}

    def prepare(directory):
        work = directory / "work"
        work.mkdir()
        result = produce(work)
        outputs = set()
        payload = encode_result(result, work, outputs, exclude=extra)
        for name in outputs:
            source = member(work, name)
            destination = member(directory / "files", name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
        shutil.rmtree(work)  # Only this producer's completed, process-owned workspace.
        return payload

    directory, payload = StageStore().run(
        stage, inputs, prepare, code=code, files=files
    )
    for name, identity in json.loads((directory / "checkpoint.json").read_text())[
        "files"
    ].items():
        relative = Path(name).relative_to("files").as_posix()
        destination = member(workdir, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = member(directory, name)
        copy_verified(source, destination, identity)
    return decode_result(payload, workdir, result_type, extra)


def cached_file(stage, inputs, output, produce, validate, *, files, code):
    def prepare(directory):
        with tempfile.TemporaryDirectory(prefix="build-", dir=directory) as scratch:
            path = Path(scratch) / output.name
            produce(path)
            validate(path)
            path.rename(directory / output.name)
        return {}

    directory, _ = StageStore().run(stage, inputs, prepare, files=files, code=code)
    identity = json.loads((directory / "checkpoint.json").read_text())["files"][
        output.name
    ]
    copy_verified(directory / output.name, output, identity)


def copy_verified(source, destination, identity):
    if destination.exists():
        if sha256_file(destination) != identity["sha256"]:
            raise ValueError(
                "refusing to overwrite changed build output: " + str(destination)
            )
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="copying-", dir=destination.parent
    ) as temporary:
        candidate = Path(temporary) / "artifact"
        checksum, size = hashlib.sha256(), 0
        with source.open("rb") as original, candidate.open("xb") as target:
            while data := original.read(1024 * 1024):
                checksum.update(data)
                size += len(data)
                target.write(data)
        if size != identity["size"] or checksum.hexdigest() != identity["sha256"]:
            raise ValueError("checkpoint output changed during materialization")
        os.link(candidate, destination)


def artifact_checkpoint(artifacts, context, *, manifest=None):
    """Preserve/restore completed manual builds for any approved file/tree format."""
    from ingestion.common.publication_sources import input_digest

    declaration = [
        {k: value for k, value in artifact.items() if k != "path"}
        | {"name": Path(artifact["path"]).name}
        for artifact in artifacts
    ]
    inputs = {"context": context, "artifacts": declaration}
    if manifest is not None:
        path = Path(manifest)
        value = verify(path)
        if (
            value["binding"]["stage"] != "manual-artifacts"
            or value["binding"]["inputs"] != inputs
            or implementation(value["binding"]["implementation"])
            != value["binding"]["implementation"]
        ):
            raise ValueError(
                "manual artifact checkpoint no longer matches current publication inputs"
            )
        for index, artifact in enumerate(artifacts):
            destination = Path(artifact["path"])
            source = member(path.parent, f"{index}/{destination.name}")
            if destination.exists():
                if input_digest(destination) != input_digest(source):
                    raise ValueError(
                        "artifact changed after checkpoint: " + str(destination)
                    )
            else:
                if destination.resolve().is_relative_to(REPO):
                    raise ValueError(
                        "restored publication artifacts must stay outside the repository"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    with (
                        destination.open("xb") as stream,
                        source.open("rb") as original,
                    ):
                        shutil.copyfileobj(original, stream)
        return str(path)

    def produce(directory):
        for index, artifact in enumerate(artifacts):
            source = Path(artifact["path"])
            target = directory / str(index) / source.name
            target.parent.mkdir()
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copyfile(source, target)
        return {}

    directory, _ = StageStore().run(
        "manual-artifacts",
        inputs,
        produce,
        files={
            str(index): Path(artifact["path"])
            for index, artifact in enumerate(artifacts)
        },
        code=("scripts/publishing_concierge.py",),
    )
    return str(directory / "checkpoint.json")
