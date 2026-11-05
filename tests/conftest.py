"""Persistent build evidence belongs to each test's isolated scratch directory."""
from copy import deepcopy
from functools import lru_cache

import hcl2
import pytest
import yaml


@pytest.fixture
def no_ambient_credentials(monkeypatch):
    import google.auth
    from unittest.mock import Mock

    credentials = Mock(side_effect=AssertionError("unexpected ambient cloud credential discovery"))
    monkeypatch.setattr(google.auth, "default", credentials)
    yield
    credentials.assert_not_called()


@pytest.fixture(scope="session", autouse=True)
def parsed_policy_documents():
    """Reuse pure parses across contracts, preserving independent mutable results.

    Keys contain bytes/text and every HCL option, never a filename or mtime.
    Changed fixtures and malformed documents still reach the real parser.
    """
    yaml_parse, hcl_parse = yaml.safe_load, hcl2.loads
    yaml_cached = lru_cache(maxsize=512)(yaml_parse)

    @lru_cache(maxsize=512)
    def hcl_cached(text, options):
        return hcl_parse(text, serialization_options=None if options is None else hcl2.SerializationOptions(**dict(options)))

    def yaml_document(source):
        return deepcopy(yaml_cached(source)) if isinstance(source, (str, bytes)) else yaml_parse(source)

    def hcl_document(text, *, serialization_options=None):
        options = tuple(vars(serialization_options).items()) if serialization_options is not None else None
        return deepcopy(hcl_cached(text, options))

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(yaml, "safe_load", yaml_document)
        patch.setattr(hcl2, "loads", hcl_document)
        yield yaml_cached, hcl_cached


@pytest.fixture(autouse=True)
def publication_checkpoint_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARED_DATASETS_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
