#!/usr/bin/env python3
"""Fingerprint the exact local bundle or saved Terraform plan before mutation."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    entries = []
    for source in paths:
        if source.is_symlink():
            raise ValueError("deployment evidence must not contain symlinks")
        files = sorted(source.rglob("*")) if source.is_dir() else [source]
        for path in files:
            if path.is_symlink():
                raise ValueError("deployment evidence must not contain symlinks")
            if path.is_file():
                label = str(path.relative_to(source)) if source.is_dir() else source.name
                entries.append((f"{source.name}/{label}", path))
    if not entries:
        raise ValueError("deployment evidence is empty")
    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        raise ValueError("deployment evidence names collide")
    for name, path in sorted(entries):
        data = path.read_bytes()
        digest.update(name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(fingerprint(args.paths))


if __name__ == "__main__":
    main()
