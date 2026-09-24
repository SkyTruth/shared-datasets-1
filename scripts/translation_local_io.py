"""Per-file candidate/replace semantics for local translation tools.

This detects edits before replacement; it is not a lock against arbitrary editors
or a transaction across several outputs. A later file failure leaves earlier
committed files in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


class TranslationLocalIOError(OSError):
    """A local translation write would overwrite protected or changed bytes."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aliases(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve() or (
        left.exists() and right.exists() and left.samefile(right)
    )


def validate_paths(*, inputs: Sequence[Path], outputs: Sequence[Path]) -> None:
    """Check all planned writes before any of them starts (including reports)."""
    for index, output in enumerate(outputs):
        if output.is_symlink():
            raise TranslationLocalIOError(f"translation output must not be a symlink: {output}")
        for protected in [*inputs, *outputs[:index]]:
            if aliases(output, protected):
                raise TranslationLocalIOError(f"translation output aliases protected path: {output} -> {protected}")


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    resolved: Path
    sha256: str | None

    @classmethod
    def capture(cls, path: Path) -> FileSnapshot:
        return cls(path, path.resolve(), file_sha256(path) if path.exists() else None)

    def verify(self) -> None:
        if self != self.capture(self.path):
            raise TranslationLocalIOError(f"translation input/output changed while working: {self.path}; rerun with current files")


def snapshots(paths: Sequence[Path], *, observed: Sequence[FileSnapshot] = ()) -> tuple[FileSnapshot, ...]:
    # Keep the first observation, including before a caller parses schema/CSV.
    captured = {snapshot.path.absolute(): snapshot for snapshot in observed}
    for path in paths:
        if path.absolute() not in captured:
            captured[path.absolute()] = FileSnapshot.capture(path)
    return tuple(captured.values())


@contextmanager
def candidate_output(
    destination: Path, *, expected: Sequence[FileSnapshot] = (), protected: Sequence[Path] = ()
) -> Iterator[Path]:
    """Caller writes and validates the candidate; normal exit commits one file."""
    validate_paths(inputs=protected, outputs=[destination])
    destination_snapshot = FileSnapshot.capture(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.translation-", dir=destination.parent)
    os.close(fd)
    candidate = Path(name)
    try:
        yield candidate
        for snapshot in [*expected, destination_snapshot]:
            snapshot.verify()
        validate_paths(inputs=protected, outputs=[destination])
        os.replace(candidate, destination)
    finally:
        candidate.unlink(missing_ok=True)


def write_json(path: Path, payload: Mapping[str, Any], *, protected: Sequence[Path] = (), expected: Sequence[FileSnapshot] = ()) -> None:
    with candidate_output(path, protected=protected, expected=expected) as candidate:
        candidate.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
