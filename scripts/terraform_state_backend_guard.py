#!/usr/bin/env python3
"""Fail closed until a Terraform root's isolated backend state exists."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence


STATE_BUCKET = "skytruth-shared-datasets-1-terraform-state"
STATE_PREFIXES = {
    "prod": "000-system/terraform/state/prod/default.tfstate",
    "preview": "000-system/terraform/state/preview/default.tfstate",
}


def state_uri(root: str) -> str:
    try:
        return f"gs://{STATE_BUCKET}/{STATE_PREFIXES[root]}"
    except KeyError as exc:
        raise ValueError(f"unknown Terraform root: {root}") from exc


def check_state_exists(
    root: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    uri = state_uri(root)
    command = ["gcloud", "storage", "objects", "describe", uri, "--format=value(generation)"]
    result = runner(command, capture_output=True, text=True)
    generation = result.stdout.strip()
    if result.returncode != 0 or not generation.isdigit():
        detail = (result.stderr or result.stdout or "state object is missing").strip()
        raise RuntimeError(
            f"Refusing Terraform {root} initialization because isolated state is not ready at {uri}: {detail}. "
            "Run the protected Terraform State Migration workflow from main."
        )
    return generation


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", choices=sorted(STATE_PREFIXES))
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        generation = check_state_exists(args.root, runner=runner)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Terraform {args.root} state generation: {generation}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
