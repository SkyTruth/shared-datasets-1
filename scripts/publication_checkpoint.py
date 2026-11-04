#!/usr/bin/env python3
"""Validate a complete local publication build plan, then resume verified stages."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common.publication_readiness import validate_ready_report
from ingestion.common.publication_sources import input_digest
from ingestion.common.runtime import sha256_file
from ingestion.common.stage_checkpoints import REPO, StageStore, implementation, member
from scripts import release_feature_model as model


def preflight(plan):
    required = {"asset_slug", "release", "source_sha256", "readiness_report", "stages"}
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or required - plan.keys()
    ):
        raise ValueError("complete schema_version=1 publication stage plan required")
    if not isinstance(plan["stages"], list) or not plan["stages"]:
        raise ValueError("nonempty stage list required")
    stages, executables = {}, {}
    for stage in plan["stages"]:
        if (
            not isinstance(stage, dict)
            or {"name", "inputs", "code", "command", "checks", "outputs"} - stage.keys()
        ):
            raise ValueError(
                "every stage requires name, inputs, code, command, checks and outputs"
            )
        name = stage["name"]
        if not isinstance(stage["inputs"], dict) or any(
            not isinstance(stage[key], list)
            or not all(isinstance(value, str) for value in stage[key])
            for key in ("code", "outputs")
        ):
            raise ValueError(
                "stage inputs must be a mapping; code and outputs must be string lists"
            )
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            or name in stages
        ):
            raise ValueError("stage names must be unique slugs")
        if not stage["outputs"] or len(set(stage["outputs"])) != len(stage["outputs"]):
            raise ValueError("each stage requires unique expected outputs")
        for output in stage["outputs"]:
            member(Path("/checkpoint-placeholder"), output)
        if not stage["code"] or not stage["checks"]:
            raise ValueError(
                "every stage requires producer code and validation commands"
            )
        implementation(stage["code"])
        tools = stage.get("tools", [])
        if not isinstance(tools, list) or not all(
            isinstance(tool, str) for tool in tools
        ):
            raise ValueError("stage tools must be an executable name list")
        for command in [stage["command"], *stage["checks"]]:
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(v, str) for v in command)
                or shutil.which(command[0]) is None
            ):
                raise ValueError("missing or invalid stage executable: " + str(command))
            for token in re.findall(r"\{input:([^}]+)\}", " ".join(command)):
                if token not in stage["inputs"]:
                    raise ValueError("undeclared stage input: " + token)
        executables[name] = {}
        for executable in {
            stage["command"][0],
            *(command[0] for command in stage["checks"]),
            *tools,
        }:
            resolved = shutil.which(executable)
            if resolved is None:
                raise ValueError("missing stage tool: " + executable)
            path = Path(resolved).resolve()
            executables[name][executable] = {
                "path": str(path),
                "sha256": sha256_file(path),
            }
        stages[name] = stage
    ordered, pending = [], dict(stages)
    while pending:
        progress = False
        for name, stage in list(pending.items()):
            dependencies = []
            for role, source in stage["inputs"].items():
                if not isinstance(source, dict):
                    raise ValueError(
                        "stage inputs must declare file pins or prior stage outputs"
                    )
                if set(source) == {"stage", "output"}:
                    upstream = stages.get(source["stage"])
                    if upstream is None or source["output"] not in upstream["outputs"]:
                        raise ValueError("unknown dependency output: " + role)
                    dependencies.append(source["stage"])
                elif set(source) == {"path", "sha256"}:
                    if (
                        not isinstance(source["sha256"], str)
                        or not Path(source["path"]).exists()
                        or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
                    ):
                        raise ValueError("missing or unpinned stage input: " + role)
                else:
                    raise ValueError("invalid stage input declaration: " + role)
            if all(dependency in ordered for dependency in dependencies):
                ordered.append(name)
                del pending[name]
                progress = True
        if not progress:
            raise ValueError("publication stage dependency cycle")
    report = validate_ready_report(
        Path(plan["readiness_report"]),
        asset_slug=plan["asset_slug"],
        source_sha256=plan["source_sha256"],
        release=plan["release"],
    )
    analysis = Path(report["assets"][plan["asset_slug"]]["analysis"])
    binding = json.loads((analysis / "analysis.json").read_text())["binding"]
    accepted_sources = {plan["source_sha256"], binding["inputs"].get("source_sha256")}
    if not any(
        stage["inputs"].get("source", {}).get("sha256") in accepted_sources - {None}
        and "{input:source}" in " ".join(stage["command"])
        for stage in stages.values()
    ):
        raise ValueError(
            "stage plan must consume the readiness-bound source as {input:source}"
        )
    for stage in stages.values():
        for source in stage["inputs"].values():
            if (
                "path" in source
                and input_digest(Path(source["path"])) != source["sha256"]
            ):
                raise ValueError("stage input checksum changed: " + source["path"])
    # Readiness is a prerequisite, never authority to publish these outputs.
    proof = {
        slug: {
            "analysis_sha256": value["analysis_sha256"],
            "review_inputs": value["review_inputs"],
        }
        for slug, value in report["assets"].items()
    }
    return stages, ordered, proof, executables


def run(plan, *, check_only=False):
    stages, ordered, proof, executables = preflight(plan)
    if check_only:
        return {"state": "READY", "stages": ordered, "executed": 0}
    store, completed = StageStore(plan.get("checkpoint_dir")), {}
    for name in ordered:
        stage = stages[name]
        files = {
            role: Path(value["path"])
            if "path" in value
            else completed[value["stage"]] / value["output"]
            for role, value in stage["inputs"].items()
        }
        files = {role: path.resolve() for role, path in files.items()}
        pins = {
            role: value["sha256"]
            for role, value in stage["inputs"].items()
            if "path" in value
        }

        def produce(directory, stage=stage, files=files):
            def argv(command):
                result = []
                for token in command:
                    token = token.replace("{output_dir}", str(directory))
                    for role, path in files.items():
                        token = token.replace("{input:" + role + "}", str(path))
                    result.append(token)
                result[0] = executables[name][command[0]]["path"]
                return result

            subprocess.run(argv(stage["command"]), cwd=REPO, check=True)
            for command in stage["checks"]:
                subprocess.run(argv(command), cwd=REPO, check=True)
            for output in stage["outputs"]:
                if not member(directory, output).exists():
                    raise ValueError("stage omitted declared output: " + output)
            return {"outputs": stage["outputs"]}

        tool_files = {
            "tool/" + executable: Path(identity["path"])
            for executable, identity in executables[name].items()
        }
        tool_pins = {
            "tool/" + executable: identity["sha256"]
            for executable, identity in executables[name].items()
        }
        directory, _ = store.run(
            name,
            {
                "asset_slug": plan["asset_slug"],
                "release": plan["release"],
                "readiness": proof,
                "declaration": stage,
                "executables": executables[name],
            },
            produce,
            files={**files, **tool_files},
            expected_files={**pins, **tool_pins},
            code=stage["code"],
        )
        completed[name] = directory
    return {
        "state": "READY",
        "stages": {
            name: str(path / "checkpoint.json") for name, path in completed.items()
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(json.loads(args.plan.read_text()), check_only=args.check_only)
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        result = {"state": "INCOMPLETE", "error": str(exc)}
    print(model.canonical_json(result))
    return 0 if result["state"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
