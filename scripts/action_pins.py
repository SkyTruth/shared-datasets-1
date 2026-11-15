"""Reviewed upstream Action revisions, shared by workflow policy and its tests."""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def pinned(reference):
    repository, _ = reference.split("@")
    return repository + "@" + json.loads((ROOT / ".github/action-pins.json").read_text())[reference]


def check(root):
    import yaml

    pins = json.loads((root / ".github/action-pins.json").read_text())
    allowed = {ref.split("@")[0] + "@" + sha for ref, sha in pins.items()
               if re.fullmatch(r"[0-9a-f]{40}", sha)}
    if len(allowed) != len(pins):
        raise ValueError("Action pins must name distinct full commit SHAs")
    def references(value):
        if isinstance(value, dict):
            if "uses" in value:
                yield value["uses"]
            for child in value.values():
                yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    for path in sorted((root / ".github").rglob("*.y*ml")):
        for reference in references(yaml.safe_load(path.read_text())):
            if not isinstance(reference, str) or (not reference.startswith("./") and reference not in allowed):
                raise ValueError(f"{path.relative_to(root)}: unreviewed Action revision {reference}")


if __name__ == "__main__":
    check(ROOT)
