"""Budget actual Terraform-rendered IAM expressions without cloud credentials."""
from __future__ import annotations

from functools import cache
import json
from pathlib import Path
import re
import shutil

import pytest

from terraform_helpers import evaluate, load_terraform, resources

PROD = Path(__file__).resolve().parents[1] / "terraform/envs/prod"
# https://cloud.google.com/iam/quotas#limits (allow and deny conditions).
MAX_LOGICAL_OPERATORS = 12
# A conservative repository budget, not a documented Cloud Storage API limit.
# The IAM allow-policy/Expr docs do not publish a per-expression length limit.
MAX_EXPRESSION_BYTES = 2048
# Ignore CEL strings/comments and !=; count unary ! as well as && and ||.
CEL_TOKENS = re.compile(
    r"//[^\n]*|/\*.*?\*/|[rRbB]{0,2}(?:"
    r"\"\"\"(?:\\.|[^\\])*?\"\"\"|'''(?:\\.|[^\\])*?'''|"
    r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')|!=|&&|\|\||!",
    re.DOTALL,
)


def operator_count(expression):
    return sum(token[0] in {"&&", "||", "!"} for token in CEL_TOKENS.finditer(expression))


def check_budget(expression):
    count = operator_count(expression)
    size = len(expression.encode("utf-8"))
    assert count <= MAX_LOGICAL_OPERATORS, f"{count} logical operators > {MAX_LOGICAL_OPERATORS}: {expression}"
    assert size <= MAX_EXPRESSION_BYTES, f"{size} expression bytes > repository budget {MAX_EXPRESSION_BYTES}"


def condition_blocks(body):
    """Include disabled/dynamic conditions too; a future activation must be safe."""
    if isinstance(body, dict):
        for key, value in body.items():
            if key in {"condition", "denial_condition"}:
                for block in value if isinstance(value, list) else [value]:
                    if "expression" in block:
                        yield block
            yield from condition_blocks(value)
    elif isinstance(body, list):
        for value in body:
            yield from condition_blocks(value)
    # A dynamic condition's content is nested under "content", not "condition".
    if isinstance(body, dict) and "content" in body:
        for block in body["content"]:
            if "expression" in block:
                yield block


@cache
def rendered_conditions(prod=PROD):
    documents = [load_terraform(path) for path in sorted(prod.glob("*.tf"))]
    locals_ = {name: value for doc in documents for block in doc.get("locals", []) for name, value in block.items()}
    variables = {name: body["default"] for doc in documents for block in doc.get("variable", [])
                 for name, body in block.items() if "default" in body}
    declarations = {f"{kind}.{name}": body for doc in documents for kind, name, body in resources(doc)}
    # Replace only known external leaves; all locals, files, collections and
    # templates still execute in Terraform. New unresolved references fail.
    replacements = {"data.google_project.current.number": "local.iam_project_number",
                    "google_storage_bucket.dataset_usage_state.name": "local.iam_usage_state_bucket"}
    locals_["iam_project_number"] = "12695949518"
    locals_["iam_usage_state_bucket"] = declarations["google_storage_bucket.dataset_usage_state"]["name"]
    for doc in documents:
        for block in doc.get("module", []):
            for name, body in block.items():
                if body["source"] == "../../modules/service_account":
                    email = f"{body['account_id']}@{body['project_id']}.iam.gserviceaccount.com"
                    for field, value in {"email": email, "member": "serviceAccount:" + email}.items():
                        key = f"iam_{name}_{field}"
                        locals_[key] = value
                        replacements[f"module.{name}.{field}"] = "local." + key

    def rewrite(value):
        if isinstance(value, str):
            value = value.replace("${path.module}", str(prod))
            for before, after in replacements.items():
                value = value.replace(before, after)
            return value
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    def render(value):
        selected = {"iam_result": rewrite(value)}
        pending = [selected["iam_result"]]
        while pending:
            for name in re.findall(r"\blocal\.([A-Za-z0-9_]+)", json.dumps(pending.pop())):
                if name not in selected:
                    selected[name] = rewrite(locals_[name])
                    pending.append(selected[name])
        return evaluate(selected, "local.iam_result", variables=variables)

    result = {}
    for address, body in declarations.items():
        if "_iam_" not in address:
            continue
        if "attribute_condition" in body:
            result[f"{address}/attribute_condition"] = render(body["attribute_condition"])
        for index, condition in enumerate(condition_blocks(body)):
            expression = condition["expression"]
            if "each." in expression:
                instances = render(body["for_each"])
                for key, value in instances.items():
                    locals_["iam_each"] = {"key": key, "value": value}
                    result[f"{address}[{json.dumps(key)}]/{index}"] = render(expression.replace("each.", "local.iam_each."))
            else:
                result[f"{address}/{index}"] = render(expression)
    assert result, "No production IAM conditions discovered"
    return result


def test_all_production_iam_conditions_fit_rendered_budgets():
    failures = []
    for address, expression in rendered_conditions().items():
        try:
            if address.endswith("/attribute_condition"):
                # WIF provider conditions have their own 4096-character limit;
                # the binding's 12-operator limit does not apply to federation.
                # https://cloud.google.com/iam/docs/reference/rest/v1/projects.locations.workloadIdentityPools.providers
                print(f"{address}: operators={operator_count(expression)} (WIF), characters={len(expression)}/4096")
                assert len(expression) <= 4096, "WIF attribute condition exceeds 4096 characters"
            else:
                print(f"{address}: operators={operator_count(expression)}/{MAX_LOGICAL_OPERATORS}, bytes={len(expression.encode())}/{MAX_EXPRESSION_BYTES}")
                check_budget(expression)
        except AssertionError as exc:
            failures.append(f"{address}: {exc}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("expression,count", [
    ("true", 0), ("!false && true || false", 3),
    ("resource.name != 'a && b || !c'", 0),
    ('resource.name == "escaped\\\" || !" && true', 1),
    ("resource.name == r'''&& || !''' || true // && !\n/* || ! */", 1),
    (" || ".join(["true"] * 13), 12),
])
def test_operator_tokens(expression, count):
    assert operator_count(expression) == count
    check_budget(expression)


def test_rejected_production_union_is_over_budget():
    expressions = rendered_conditions()
    canonical = expressions["google_storage_bucket_iam_member.github_readonly_canonical_object_viewer/0"]
    pending = expressions["google_storage_bucket_iam_member.github_readonly_pending_publish_viewer/0"]
    with pytest.raises(AssertionError, match=r"\d+ logical operators > 12"):
        check_budget(f"({canonical}) || ({pending})")


def test_split_preserves_exact_original_union():
    expressions = rendered_conditions()
    assert expressions["google_storage_bucket_iam_member.github_readonly_canonical_object_viewer/0"] == expressions[
        "google_storage_bucket_iam_member.shared_datasets_publisher_object_user/0"]
    variables = {name: body for block in load_terraform(PROD / "variables.tf")["variable"] for name, body in block.items()}
    bucket_name = variables["bucket_name"]["default"]
    pending = expressions["google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_viewer/0"]
    assert expressions["google_storage_bucket_iam_member.github_readonly_pending_publish_viewer/0"] == (
        f"resource.name == 'projects/_/buckets/{bucket_name}' || ({pending})")


def test_new_prefixes_cannot_bypass_shared_condition_guard(tmp_path):
    prod = tmp_path / "terraform/envs/prod"
    prod.mkdir(parents=True)
    for path in [*PROD.glob("*.tf"), *PROD.glob("*.json")]:
        shutil.copyfile(path, prod / path.name)
    shutil.copytree(PROD.parents[2] / "catalog", tmp_path / "catalog")
    categories = tmp_path / "catalog/categories.yaml"
    # Exercise the actual YAML -> Terraform local -> rendered IAM boundary.
    categories.write_text(categories.read_text() + "\n" + "\n".join(
        f'  "90{index}-fixture": {{description: "guard fixture", subcategories: {{}}}}' for index in range(4)))
    expressions = rendered_conditions(prod)
    for name in ("shared_datasets_publisher_object_user", "github_readonly_canonical_object_viewer"):
        expression = expressions[f"google_storage_bucket_iam_member.{name}/0"]
        with pytest.raises(AssertionError, match=r"\d+ logical operators > 12"):
            check_budget(expression)


def test_expression_length_budget_counts_utf8_bytes():
    check_budget(" " * MAX_EXPRESSION_BYTES)
    with pytest.raises(AssertionError, match="expression bytes > repository budget"):
        check_budget("é" * (MAX_EXPRESSION_BYTES // 2 + 1))
