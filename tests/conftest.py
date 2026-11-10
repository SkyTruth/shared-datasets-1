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


@pytest.fixture(scope="session")
def historical_git_repo(tmp_path_factory):
    """A self-contained historical graph: never require the caller's Git history."""
    import os
    import subprocess
    import yaml
    from types import SimpleNamespace

    root = tmp_path_factory.mktemp("historical-graph")
    workflows = root / ".github/workflows"
    workflows.mkdir(parents=True)
    names = {"wdpa": ("wdpa-monthly", "Deploy tested WDPA ingestion image"),
             "mpatlas": ("mpatlas-monthly", "Deploy tested MPAtlas ingestion image"),
             "eamlis": ("eamlis-monthly", "Deploy tested EAMLIS ingestion image"),
             "sea-ice": ("sea-ice-daily", "Deploy tested sea ice ingestion image"),
             "dataset-usage": ("dataset-usage", "Deploy tested dataset usage image and private telemetry resources")}
    documents = {target + "-deploy.yml": {"jobs": {"deploy": {"name": name, "runs-on": "ubuntu-latest"}}}
                 for target, name in names.values()}
    documents["ci.yml"] = {"jobs": {key: {"uses": "./.github/workflows/" + target + "-deploy.yml"}
                                    for key, (target, _) in names.items()}}
    for filename, document in documents.items():
        (workflows / filename).write_text(yaml.safe_dump(document))
    environment = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.test",
                   "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.test"}
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), "-c", "core.hooksPath=/dev/null",
            "-c", "commit.gpgsign=false", *args], env=environment, text=True).strip()
    git("init", "-b", "main")
    git("add", ".")
    git("commit", "-m", "historical graph")
    return SimpleNamespace(root=root, sha=git("rev-parse", "HEAD"), documents=documents)


@pytest.fixture
def historical_graph(historical_git_repo, monkeypatch):
    from functools import partial
    from scripts import slack_incidents
    monkeypatch.setattr(slack_incidents, "job_targets", partial(slack_incidents.job_targets, historical_git_repo.root))
    return historical_git_repo
