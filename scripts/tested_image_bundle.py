"""Strict, streamed Docker-save image boundary shared by CI and deployment."""
from __future__ import annotations

import hashlib
import gzip
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile

TARGETS = frozenset({"eamlis-monthly", "sea-ice-daily", "catalog-viewer"})
MAX_IMAGE = 2 * 1024 ** 3
MAX_JSON = 1024 ** 2
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")


class ImageError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ImageError(message)


def object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read_json(raw):
    require(len(raw) <= MAX_JSON, "image JSON exceeds limit")
    return json.loads(raw, object_pairs_hook=object_pairs)


def hash_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 ** 2), b""):
        digest.update(chunk)
    return digest.hexdigest()


def hash_file(path):
    with Path(path).open("rb") as stream:
        return hash_stream(stream)


def source_tag(target, sha):
    require(target in TARGETS and SHA.fullmatch(sha), "invalid retained image target or revision")
    return f"shared-datasets-preflight/{target}:{sha}"


def validate_manifest(manifest, sha):
    require(isinstance(manifest, dict) and set(manifest) == {"schema_version", "tested_sha", "images"}
            and type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
            and manifest["tested_sha"] == sha and SHA.fullmatch(sha), "invalid tested image manifest identity")
    images = manifest["images"]
    require(isinstance(images, dict) and images and set(images) <= TARGETS, "invalid retained image selection")
    for target, image in images.items():
        require(isinstance(image, dict) and set(image) == {"image_id", "source_tag", "archive", "archive_sha256", "archive_size", "platform"},
                "invalid retained image fields")
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", str(image["image_id"]))
                and image["source_tag"] == source_tag(target, sha)
                and image["archive"] == f"{target}.docker.tar"
                and DIGEST.fullmatch(str(image["archive_sha256"]))
                and type(image["archive_size"]) is int and 0 < image["archive_size"] <= MAX_IMAGE
                and image["platform"] == "linux/amd64", "retained image is not exact tested Linux/amd64 bytes")
    return images


def verify_archive(path, image):
    """Never extract image TAR members to the host; verify config and every rootfs layer."""
    path = Path(path)
    require(path.stat().st_size == image["archive_size"] <= MAX_IMAGE
            and hash_file(path) == image["archive_sha256"], "retained image archive digest or size mismatch")
    with tarfile.open(path, mode="r:") as archive:
        members = archive.getmembers()
        require(len(members) <= 2048, "too many Docker-save members")
        names = [member.name for member in members]
        require(len(names) == len(set(names)), "duplicate Docker-save member")
        require(all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                    and "\\" not in name and name == str(PurePosixPath(name)) for name in names), "unsafe Docker-save member")
        by_name = {member.name: member for member in members}

        def metadata(name):
            require(name in by_name and by_name[name].isfile() and by_name[name].size <= MAX_JSON,
                    "missing or invalid Docker-save metadata: " + name)
            return read_json(archive.extractfile(by_name[name]).read())

        manifest = metadata("manifest.json")
        require(isinstance(manifest, list) and len(manifest) == 1 and isinstance(manifest[0], dict)
                and set(manifest[0]) == {"Config", "RepoTags", "Layers"}, "Docker-save must contain exactly one image")
        saved = manifest[0]
        config_name = image["image_id"].removeprefix("sha256:") + ".json"
        require(saved["Config"] == config_name and saved["RepoTags"] == [image["source_tag"]],
                "Docker-save config or tag differs from tested image")
        config = metadata(config_name)
        require(hash_stream(archive.extractfile(by_name[config_name])) == image["image_id"].removeprefix("sha256:")
                and config.get("os") == "linux" and config.get("architecture") == "amd64", "Docker-save config identity or platform mismatch")
        layers = saved["Layers"]
        diff_ids = config.get("rootfs", {}).get("diff_ids")
        require(config.get("rootfs", {}).get("type") == "layers" and isinstance(layers, list)
                and layers and isinstance(diff_ids, list) and len(layers) == len(diff_ids),
                "Docker-save rootfs contract mismatch")
        allowed = {"manifest.json", config_name}
        for name, diff_id in zip(layers, diff_ids):
            require(isinstance(name, str) and re.fullmatch(r"[0-9a-f]{64}/layer\.tar", name)
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", str(diff_id))
                    and name in by_name and by_name[name].isfile(), "invalid Docker-save rootfs layer")
            require("sha256:" + hash_stream(archive.extractfile(by_name[name])) == diff_id, "Docker-save rootfs layer digest mismatch")
            directory = name.partition("/")[0]
            allowed.update({name, directory, directory + "/VERSION", directory + "/json"})
        if "repositories" in by_name:
            repository, _, tag = image["source_tag"].partition(":")
            require(metadata("repositories") == {repository: {tag: layers[-1].partition("/")[0]}}, "Docker-save contains extra repository tags")
            allowed.add("repositories")
        require(set(names) <= allowed and all(member.isfile() or member.isdir() for member in members),
                "unexpected Docker-save member or link")
        require(all((member.isdir() and member.name in {layer.partition('/')[0] for layer in layers})
                    or (member.isfile() and (member.name in layers or member.size <= MAX_JSON)) for member in members),
                "invalid Docker-save member type or size")


def canonicalize_archive(raw_path, destination, image_id, tag):
    """Normalize Docker 20/28 transports without changing tested config or rootfs bytes.

    Modern Docker-save adds OCI graphs and legacy metadata, and may compress
    layers. Only the single tested config and its ordered rootfs are retained;
    unused exporter metadata is never fed to Docker by the consumer.
    """
    require(Path(raw_path).stat().st_size <= MAX_IMAGE, "raw saved image exceeds limit")
    with tarfile.open(raw_path, mode="r:") as saved:
        members = saved.getmembers()
        names = [member.name for member in members]
        require(len(members) <= 2048 and len(names) == len(set(names)), "duplicate or excessive raw Docker-save members")
        require(all(not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
                    and "\\" not in name and (member.isfile() or member.isdir()) for name, member in zip(names, members)),
                "unsafe raw Docker-save member or link")
        entries = {member.name: member for member in members}

        def small_member(name):
            require(name in entries and entries[name].isfile() and entries[name].size <= MAX_JSON,
                    "missing or oversized raw Docker-save metadata")
            return saved.extractfile(entries[name]).read()

        manifest = read_json(small_member("manifest.json"))
        require(isinstance(manifest, list) and len(manifest) == 1 and isinstance(manifest[0], dict)
                and {"Config", "RepoTags", "Layers"} <= set(manifest[0]) <= {"Config", "RepoTags", "Layers", "LayerSources", "Parent"},
                "raw Docker-save must contain exactly one supported image")
        image = manifest[0]
        require(image["RepoTags"] == [tag], "raw saved image has another or additional tag")
        config_bytes = small_member(image["Config"])
        config = read_json(config_bytes)
        require("sha256:" + hashlib.sha256(config_bytes).hexdigest() == image_id
                and config.get("os") == "linux" and config.get("architecture") == "amd64", "raw saved config differs from tested image")
        layers = image["Layers"]
        diff_ids = config.get("rootfs", {}).get("diff_ids")
        require(config.get("rootfs", {}).get("type") == "layers" and isinstance(layers, list) and layers
                and isinstance(diff_ids, list) and len(layers) == len(diff_ids)
                and all(isinstance(name, str) and name in entries and entries[name].isfile() for name in layers)
                and all(re.fullmatch(r"sha256:[0-9a-f]{64}", str(digest)) for digest in diff_ids), "invalid raw saved rootfs")
        config_name = image_id.removeprefix("sha256:") + ".json"
        canonical_layers = [digest.removeprefix("sha256:") + "/layer.tar" for digest in diff_ids]
        total = 0
        written = set()
        with tarfile.open(destination, mode="x:") as output:
            def add(name, size, stream):
                nonlocal total
                total += ((size + 511) // 512 + 1) * 512
                require(total + 10240 <= MAX_IMAGE, "canonical saved image exceeds limit")
                info = tarfile.TarInfo(name)
                info.size = size
                output.addfile(info, stream)

            add(config_name, len(config_bytes), io.BytesIO(config_bytes))
            for name, digest, canonical_name in zip(layers, diff_ids, canonical_layers):
                # A process-owned temporary layer allows streaming gzip while
                # bounding expansion and hashing before it enters the archive.
                with saved.extractfile(entries[name]) as raw, tempfile.TemporaryFile(dir=Path(destination).parent) as layer:
                    magic = raw.read(2)
                    raw.seek(0)
                    stream = gzip.GzipFile(fileobj=raw) if magic == b"\x1f\x8b" else raw
                    size = 0
                    hashed = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 ** 2), b""):
                        size += len(chunk)
                        require(size + total + 10240 <= MAX_IMAGE, "saved layer expansion exceeds limit")
                        hashed.update(chunk)
                        layer.write(chunk)
                    require("sha256:" + hashed.hexdigest() == digest, "raw saved rootfs layer digest mismatch")
                    if canonical_name not in written:
                        layer.seek(0)
                        add(canonical_name, size, layer)
                        written.add(canonical_name)
            canonical = json.dumps([{"Config": config_name, "RepoTags": [tag], "Layers": canonical_layers}], sort_keys=True).encode()
            add("manifest.json", len(canonical), io.BytesIO(canonical))
    require(Path(destination).stat().st_size <= MAX_IMAGE, "canonical saved image exceeds limit")


def pack_image(target, sha, image_id, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tag = source_tag(target, sha)
    resolved = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True).strip()
    require(resolved == image_id, "tested image tag changed before retention")
    path = directory / f"{target}.docker.tar"
    require(not path.exists(), "retained image archive already exists")
    with tempfile.TemporaryDirectory(prefix="image-save-", dir=directory) as work:
        raw_path = Path(work) / "raw.docker.tar"
        subprocess.run(["docker", "image", "save", "--output", str(raw_path), tag], check=True)
        canonicalize_archive(raw_path, path, image_id, tag)
    image = {"image_id": image_id, "source_tag": tag, "archive": path.name,
             "archive_sha256": hash_file(path), "archive_size": path.stat().st_size, "platform": "linux/amd64"}
    validate_manifest({"schema_version": 1, "tested_sha": sha, "images": {target: image}}, sha)
    verify_archive(path, image)
    return image


def load_image(path, image):
    verify_archive(path, image)
    subprocess.run(["docker", "image", "load", "--input", str(path)], check=True)
    for reference in (image["source_tag"], image["image_id"]):
        loaded = read_json(subprocess.check_output(["docker", "image", "inspect", reference]))
        require(isinstance(loaded, list) and len(loaded) == 1 and loaded[0].get("Id") == image["image_id"]
                and loaded[0].get("Os") == "linux" and loaded[0].get("Architecture") == "amd64"
                and image["source_tag"] in loaded[0].get("RepoTags", []), "loaded image differs from tested config, platform or tag")
