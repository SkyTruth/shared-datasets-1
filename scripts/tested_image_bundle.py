"""Strict, streamed Docker-save image boundary shared by CI and deployment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

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
                and layers and len(layers) == len(set(layers)) and isinstance(diff_ids, list) and len(layers) == len(diff_ids),
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


def pack_image(target, sha, image_id, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tag = source_tag(target, sha)
    resolved = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True).strip()
    require(resolved == image_id, "tested image tag changed before retention")
    path = directory / f"{target}.docker.tar"
    require(not path.exists(), "retained image archive already exists")
    subprocess.run(["docker", "image", "save", "--output", str(path), tag], check=True)
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
