"""Collection cannot depend on wall clocks or random seeds in worker imports."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def nondeterministic_collection_calls(source):
    tree = ast.parse(source)
    aliases = {alias.asname or alias.name: (f'{node.module}.{alias.name}' if isinstance(node, ast.ImportFrom) else alias.name)
               for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    problems = []

    class Collection(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            # Defaults and decorators execute at collection; test bodies do not.
            for expression in [*node.decorator_list, *node.args.defaults, *filter(None, node.args.kw_defaults)]:
                self.visit(expression)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            name = ast.unparse(node.func)
            first, _, suffix = name.partition('.')
            name = aliases.get(first, first) + ('.' + suffix if suffix else '')
            if (name.startswith(('random.', 'secrets.', 'numpy.random.'))
                    or name in {'time.time', 'time.time_ns', 'uuid.uuid1', 'uuid.uuid4'}
                    or name.endswith(('.now', '.utcnow', '.today'))):
                problems.append(node.lineno)
            self.generic_visit(node)

    Collection().visit(tree)
    return problems


def test_collection_has_no_import_time_clock_or_random_calls():
    problems = {path.name: lines for path in (ROOT / 'tests').glob('*.py')
                if (lines := nondeterministic_collection_calls(path.read_text()))}
    assert problems == {}, problems


@pytest.mark.parametrize('source', [
    'from datetime import datetime as clock\nNOW = clock.now()\n',
    'import random as r\nIDS = [r.randint(1, 99)]\n',
    'from uuid import uuid4 as identity\nVALUES = [identity()]\n',
    'import time\n@pytest.mark.parametrize("value", [time.time()])\ndef test_case(value): pass\n',
])
def test_collection_guard_rejects_dynamic_values_and_aliases(source):
    assert nondeterministic_collection_calls(source)


def test_collection_guard_allows_runtime_clocks_and_fixed_collected_values():
    assert not nondeterministic_collection_calls('from datetime import datetime\nNOW = datetime(2026, 11, 5)\ndef test_case():\n    assert datetime.now()\n')
