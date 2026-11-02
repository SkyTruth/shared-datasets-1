"""Read real HCL structure and evaluate provider-free policy expressions."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

import hcl2


OPTIONS = hcl2.SerializationOptions(
    strip_string_quotes=True, explicit_blocks=False, with_comments=False,
)


def load_terraform(path: Path) -> dict:
    return hcl2.loads(path.read_text(), serialization_options=OPTIONS)


def resources(document: dict):
    for group in document.get("resource", []):
        for kind, named in group.items():
            for name, attributes in named.items():
                yield kind, name, attributes


def evaluate(locals_: dict, expression: str, *, variables: dict | None = None):
    """Evaluate isolated expressions with no providers, remote state or credentials."""
    root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1"))
    work = root / "_scratch/terraform-policy-tests"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work) as temporary:
        directory = Path(temporary)
        config = {"locals": locals_}
        if variables:
            config["variable"] = {name: {"default": value} for name, value in variables.items()}
        (directory / "main.tf.json").write_text(json.dumps(config))
        cli_config = directory / "terraform.rc"
        cli_config.write_text("")
        completed = subprocess.run(
            ["terraform", f"-chdir={directory}", "console", "-no-color"],
            input=f"jsonencode({expression})\n", text=True, capture_output=True,
            env={"PATH": os.environ["PATH"], "CHECKPOINT_DISABLE": "1",
                 "TF_CLI_CONFIG_FILE": str(cli_config)},
            timeout=30, check=True,
        )
        assert not completed.stderr.strip(), completed.stderr
        return json.loads(json.loads(completed.stdout))
