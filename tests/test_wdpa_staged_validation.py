"""Retained staged evidence is not complete publication acceptance."""
import json
from pathlib import Path

from scripts import wdpa_processing_gate as gate

ROOT = Path(__file__).resolve().parents[1]


def test_staged_evidence_cannot_authorize_publication():
    evidence = json.loads((ROOT / "catalog/wdpa-staged-validation.json").read_text())
    assert gate.check(evidence), (
        "small/sampled evidence cannot open the production publication gate"
    )
