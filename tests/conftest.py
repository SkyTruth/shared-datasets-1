"""Persistent build evidence belongs to each test's isolated scratch directory."""
import pytest


@pytest.fixture(autouse=True)
def publication_checkpoint_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
