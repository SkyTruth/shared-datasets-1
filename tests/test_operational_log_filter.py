"""Cloud Logging's view grouping limit must not broaden the access predicate."""
from itertools import product
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r'log_id\("([^"\n]+)"\)|(resource\.(?:labels\.(?:project_id|location)|type))="([^"\n]+)"|\b(AND|OR)\b|([()])')


def tokens(text):
    result = []
    position = 0
    for match in TOKEN.finditer(text):
        if text[position:match.start()].strip():
            raise AssertionError("unsupported filter expression")
        log_id, field, value, operator, group = match.groups()
        result.append(("eq", "log_id", log_id) if log_id else ("eq", field, value) if field else operator or group)
        position = match.end()
    if text[position:].strip():
        raise AssertionError("unsupported filter expression")
    return result


def grouping_depth(text):
    # The function call's parentheses are an atomic log_id predicate, not a
    # nested Boolean group. The actual API rejected the historical depth3.
    depth = maximum = 0
    for token in tokens(text):
        if token == "(":
            depth += 1
            maximum = max(maximum, depth)
        elif token == ")":
            depth -= 1
            if depth < 0:
                raise AssertionError("unbalanced filter")
    if depth:
        raise AssertionError("unbalanced filter")
    return maximum


def parse(text):
    values = tokens(text)
    position = 0

    def atom():
        nonlocal position
        value = values[position]
        position += 1
        if value == "(":
            value = conjunction()
            if values[position] != ")":
                raise AssertionError("missing closing group")
            position += 1
        elif not isinstance(value, tuple):
            raise AssertionError("expected a supported comparison")
        return value

    def disjunction():
        nonlocal position
        value = atom()
        while position < len(values) and values[position] == "OR":
            position += 1
            value = ("or", value, atom())
        return value

    def conjunction():
        nonlocal position
        value = disjunction()
        while position < len(values) and values[position] != ")":
            if values[position] == "AND":
                position += 1
            value = ("and", value, disjunction())
        return value

    # Logging gives OR higher precedence than AND; adjacency is implicit AND.
    # https://docs.cloud.google.com/logging/docs/view/logging-query-language#boolean_operators
    tree = conjunction()
    if position != len(values):
        raise AssertionError("unconsumed filter expression")
    return tree


def evaluate(tree, entry):
    operation, left, right = tree
    if operation == "eq":
        return entry.get(left) == right
    a, b = evaluate(left, entry), evaluate(right, entry)
    return a and b if operation == "and" else a or b


class OperationalLogFilterTests(unittest.TestCase):
    def setUp(self):
        text = (ROOT / "terraform/envs/prod/operational_observer.tf").read_text()
        filters = re.findall(r"operational_observer_log_filter\s*=\s*<<-EOT\n(.*?)\nEOT", text, re.S)
        self.assertEqual(len(filters), 1)
        self.filter = filters[0].replace("${var.project_id}", "shared-datasets-1").replace("${var.region}", "us-central1")

    def test_view_filter_respects_actual_api_one_level_grouping_limit(self):
        self.assertEqual(grouping_depth(self.filter), 1)
        # Exact shape that both view creates rejected in protected setup
        # 39673870818/1: HTTP400 view filter nesting depth limit of1.
        rejected = '''resource.labels.project_id="shared-datasets-1"
resource.labels.location="us-central1"
((resource.type="cloud_run_job"
  (log_id("run.googleapis.com/stderr") OR log_id("cloudaudit.googleapis.com/activity") OR log_id("cloudaudit.googleapis.com/data_access")))
 OR (resource.type="cloud_scheduler_job" log_id("cloudscheduler.googleapis.com/executions")))'''
        self.assertEqual(grouping_depth(rejected), 3)

    def test_actual_serialized_filter_preserves_exact_family_and_resource_pairing(self):
        tree = parse(self.filter)
        allowed = {
            ("cloud_run_job", "run.googleapis.com/stderr"),
            ("cloud_run_job", "cloudaudit.googleapis.com/activity"),
            ("cloud_run_job", "cloudaudit.googleapis.com/data_access"),
            ("cloud_scheduler_job", "cloudscheduler.googleapis.com/executions"),
        }
        families = sorted({family for _, family in allowed} | {"run.googleapis.com/stdout", "cloudaudit.googleapis.com/system_event", "unrelated/log"})
        for project_id, region, resource_type, log_id in product(
            ("shared-datasets-1", "other-project", None), ("us-central1", "europe-west1", None),
            ("cloud_run_job", "cloud_scheduler_job", "cloud_run_revision", "gce_instance"), families,
        ):
            entry = {"resource.labels.project_id": project_id, "resource.labels.location": region, "resource.type": resource_type, "log_id": log_id}
            expected = project_id == "shared-datasets-1" and region == "us-central1" and (resource_type, log_id) in allowed
            with self.subTest(entry=entry):
                self.assertEqual(evaluate(tree, entry), expected)


if __name__ == "__main__":
    unittest.main()
