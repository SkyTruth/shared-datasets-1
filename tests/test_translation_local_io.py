from pathlib import Path
from unittest import mock

import pytest

from scripts import translation_local_io as local_io


@pytest.mark.parametrize("kind", ["same", "resolved", "symlink", "hardlink"])
def test_protected_aliases_preserve_input(tmp_path, kind):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    output = tmp_path / "output"
    if kind == "same":
        output = source
    elif kind == "resolved":
        output = tmp_path / "." / "source"
    elif kind == "symlink":
        output.symlink_to(source)
    else:
        output.hardlink_to(source)
    with pytest.raises(local_io.TranslationLocalIOError, match="alias|symlink"):
        with local_io.candidate_output(output, protected=[source]):
            pytest.fail("alias reached writer")
    assert source.read_bytes() == b"original"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["write", "replace", "interrupt"])
def test_candidate_failure_keeps_final(tmp_path, existing, failure):
    output = tmp_path / "output"
    if existing:
        output.write_bytes(b"verified")
    error = KeyboardInterrupt if failure == "interrupt" else OSError
    with pytest.raises(error):
        with mock.patch.object(local_io.os, "replace", side_effect=OSError("replace failure")):
            with local_io.candidate_output(output) as candidate:
                candidate.write_bytes(b"partial")
                if failure != "replace":
                    raise error("interrupted write")
    assert output.read_bytes() == b"verified" if existing else not output.exists()
    assert not list(tmp_path.glob(".*.translation-*"))


def test_original_destination_expectation_catches_edit_during_work(tmp_path):
    output = tmp_path / "translations.csv"
    output.write_bytes(b"original")
    expected = local_io.snapshots([output])
    output.write_bytes(b"human correction")
    with pytest.raises(local_io.TranslationLocalIOError, match="changed"):
        with local_io.candidate_output(output, expected=expected) as candidate:
            candidate.write_bytes(b"late machine result")
    assert output.read_bytes() == b"human correction"


def test_batch_path_preflight_and_report_are_protected(tmp_path):
    source, output = tmp_path / "source", tmp_path / "output"
    source.write_bytes(b"input")
    with pytest.raises(local_io.TranslationLocalIOError):
        local_io.validate_paths(inputs=[source], outputs=[output, output])
    with pytest.raises(local_io.TranslationLocalIOError):
        local_io.write_json(source, {"valid": True}, protected=[source])
    assert source.read_bytes() == b"input"


def test_file_hash_streams_and_success_replaces_one_file(tmp_path):
    output = tmp_path / "out"
    with local_io.candidate_output(output) as candidate:
        candidate.write_bytes(b"good")
    assert output.read_bytes() == b"good"
    assert len(local_io.file_sha256(output)) == 64
    assert isinstance(local_io.FileSnapshot.capture(output).resolved, Path)
