"""Validate workflow syntax, including GitHub's concurrency queue extension.

Pinned actionlint 1.7.12 predates queue:max. Validate that one field ourselves,
then remove only its exact source line in a disposable parser input. All other
actionlint errors remain failures; production concurrency is never rewritten.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import yaml


def parser_source(source: str) -> str:
    document = yaml.compose(source)
    if not isinstance(document, yaml.MappingNode):
        raise ValueError("workflow must be a mapping")
    removed = set()

    def concurrency(node: yaml.Node) -> None:
        if not isinstance(node, yaml.MappingNode):
            return  # Scalar concurrency is handled by actionlint.
        pairs = {key.value: value for key, value in node.value}
        if "queue" not in pairs:
            return
        if len(pairs) != len(node.value):
            raise ValueError("duplicate concurrency field")
        queue = pairs["queue"]
        cancel = pairs.get("cancel-in-progress")
        if not isinstance(queue, yaml.ScalarNode) or queue.value != "max":
            raise ValueError("concurrency.queue must be max")
        if not isinstance(cancel, yaml.ScalarNode) or cancel.tag != "tag:yaml.org,2002:bool" or cancel.value != "false":
            raise ValueError("queued mutations must explicitly set cancel-in-progress: false")
        key = next(key for key, _ in node.value if key.value == "queue")
        if key.start_mark.line != queue.end_mark.line or node.flow_style:
            raise ValueError("queue must use a single block-style scalar line")
        removed.add(key.start_mark.line)

    for key, value in document.value:
        if key.value == "concurrency":
            concurrency(value)
        elif key.value == "jobs" and isinstance(value, yaml.MappingNode):
            for _, job in value.value:
                if isinstance(job, yaml.MappingNode):
                    for job_key, job_value in job.value:
                        if job_key.value == "concurrency":
                            concurrency(job_value)
    return "".join("\n" if index in removed else line for index, line in enumerate(source.splitlines(keepends=True)))


def main() -> int:
    root = Path.cwd()
    work_root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1")) / "_scratch/workflow-syntax"
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_root) as temporary:
        files = []
        for workflow in sorted((root / ".github/workflows").glob("*.y*ml")):
            target = Path(temporary) / workflow.name
            target.write_text(parser_source(workflow.read_text()))
            files.append(str(target))
        if not files:
            raise ValueError("no workflow files found")
        return subprocess.run(["actionlint", "-shellcheck=", "-pyflakes=", *files], cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
