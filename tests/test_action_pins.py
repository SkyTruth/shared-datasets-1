import json
from pathlib import Path

import pytest

from scripts.action_pins import check


def test_all_action_references_match_the_reviewed_full_commit_inventory():
    check(Path(__file__).resolve().parents[1])


@pytest.mark.parametrize("reference", ["actions/checkout@v7", "actions/checkout@" + "b" * 40,
                                       "unreviewed/action@" + "a" * 40])
def test_mutable_and_unreviewed_action_references_are_rejected(tmp_path, reference):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/action-pins.json").write_text(json.dumps({"actions/checkout@v7": "a" * 40}))
    (tmp_path / ".github/workflows/test.yml").write_text(f"steps:\n  - uses: {reference}\n")
    with pytest.raises(ValueError, match="unreviewed Action"):
        check(tmp_path)


def test_unpinned_nested_composite_cannot_escape_inventory(tmp_path):
    (tmp_path / ".github/actions/nested").mkdir(parents=True)
    (tmp_path / ".github/action-pins.json").write_text("{}")
    (tmp_path / ".github/actions/nested/action.yml").write_text("runs:\n  steps:\n    - uses: actions/checkout@v4\n")
    with pytest.raises(ValueError, match="unreviewed Action"):
        check(tmp_path)


@pytest.mark.parametrize("key", ['"uses"', "'uses'", "uses "])
def test_yaml_key_spelling_cannot_bypass_action_pins(tmp_path, key):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/action-pins.json").write_text("{}")
    (tmp_path / ".github/workflows/test.yml").write_text(f"steps:\n  - {key}: actions/checkout@v4\n")
    with pytest.raises(ValueError, match="unreviewed Action"):
        check(tmp_path)
