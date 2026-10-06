import json
import io
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from scripts import feature_metadata_document_translate as documents
from scripts import feature_metadata_english_glossary as glossary
from scripts import feature_metadata_localization as localization
from scripts import translation_local_io as local_io


def workbook(path, texts):
    documents.write_xlsx_rows(path, [["hash", "text"], *[[localization.source_value_hash(text), text] for text in texts]])
    return path


def response(request):
    return {"model": glossary.MODEL, "usage": {"input_tokens": 42}, "answers": {key: {"type": "choice", "choice": "english_descriptor", "confidence": 0.94, "probabilities": {"english_descriptor": 0.97, "preserve": 0.02, "uncertain": 0.01}} for key in request["questions"]}}


def package(tmp_path):
    original = workbook(tmp_path / "source.xlsx", ["890. ACHANAKMAR TIGER RESERVE", "ABHL_608B", "4953. KATGHORA DIVISION"])
    directory = tmp_path / "review"
    with mock.patch.object(glossary, "call_jev", side_effect=response):
        glossary.classify([f"es={original}"], directory)
    classification = directory / "classification.json"
    approval = tmp_path / "reviewed.json"
    approval.write_text(json.dumps({"classification_sha256": local_io.file_sha256(classification), "terms": ["tiger", "reserve", "division"]}))
    return original, classification, approval


def test_cached_decisions_do_not_repeat_api_calls(tmp_path):
    original = workbook(tmp_path / "source.xlsx", ["Tiger Reserve", "Tiger Reserve 2", "Place Division"])
    with mock.patch.object(glossary, "call_jev", side_effect=response) as client:
        first = glossary.classify([f"es={original}"], tmp_path / "review")
        first_hash = local_io.file_sha256(tmp_path / "review/classification.json")
        second = glossary.classify([f"es={original}"], tmp_path / "review")
    assert client.call_count == 1
    assert first["candidates"] == second["candidates"]
    assert second["requests_sent"] == 0
    assert local_io.file_sha256(tmp_path / "review/classification.json") == first_hash


def test_only_reviewed_phrases_export_and_names_survive_reordered_return(tmp_path):
    original, classification, approval = package(tmp_path)
    before = original.read_bytes()
    result = glossary.export(classification, approval, tmp_path / "exports")
    source_glossary = Path(result["workbooks"]["es"]["path"])
    rows = documents.read_xlsx_rows(source_glossary)
    assert {row[1] for row in rows[1:]} == {"tiger reserve", "division"}
    assert len(result["excluded"]) == 1
    assert not result["excluded_are_completed_translations"]
    returned = tmp_path / "returned.xlsx"
    values = {"tiger reserve": "reserva de tigres", "division": "división"}
    documents.write_xlsx_rows(returned, [rows[0], *[[key, values[text]] for key, text in reversed(rows[1:])]])
    counts = glossary.reconstruct(tmp_path / "exports/glossary-manifest.json", [f"es={returned}"], tmp_path / "result", provenance="agent-glossary")
    actual = [json.loads(line) for line in (tmp_path / "result/reconstructed-es.jsonl").read_text().splitlines()]
    assert counts == {"es": 2}
    assert {row["value"] for row in actual} == {"890. ACHANAKMAR reserva de tigres", "4953. KATGHORA división"}
    assert {row["provenance"] for row in actual} == {"agent-glossary"}
    assert {row["review_state"] for row in actual} == {"machine_translated"}
    assert original.read_bytes() == before


def test_hash_damage_and_source_changes_are_rejected(tmp_path):
    original, classification, approval = package(tmp_path)
    result = glossary.export(classification, approval, tmp_path / "exports")
    returned = tmp_path / "bad.xlsx"
    workbook(returned, ["Foreign text"])
    with pytest.raises(ValueError, match="missing/extra/foreign"):
        glossary.reconstruct(tmp_path / "exports/glossary-manifest.json", [f"es={returned}"], tmp_path / "result", provenance="agent-glossary")
    assert not (tmp_path / "result").exists()
    workbook(original, ["Changed label"])
    with pytest.raises(ValueError, match="changed"):
        glossary.export(classification, approval, tmp_path / "new-exports")
    assert result["workbooks"]["es"]["rows"] == 2


def test_identifiers_substrings_and_non_latin_tokens_are_not_split():
    assert glossary.english_spans("ABHL_608B forester woodland forest2 éforest", {"forest", "land", "abhl"}) == []
    text = "Name  Protected  Forest (Unit-4)"
    spans = glossary.english_spans(text, {"protected", "forest"})
    assert len(spans) == 1 and spans[0]["text"] == "protected forest"
    assert glossary.render_label(text, spans, {spans[0]["hash"]: "bosque protegido"}) == "Name  bosque protegido (Unit-4)"


def test_source_workbook_preserves_canonical_line_endings(tmp_path):
    text = "Name\r\nNorth Forest\rUnit"
    original = workbook(tmp_path / "source.xlsx", [text])
    _, sources, _ = glossary.read_inputs([f"es={original}"])
    assert sources[localization.source_value_hash(text)]["text"] == text


def test_review_cannot_be_reused_for_different_report(tmp_path):
    _, classification, approval = package(tmp_path)
    classification.write_text(classification.read_text() + "\n")
    with pytest.raises(ValueError, match="exact classification"):
        glossary.export(classification, approval, tmp_path / "exports")


def test_reconstruction_cannot_hide_or_forge_source_membership(tmp_path):
    _, classification, approval = package(tmp_path)
    result = glossary.export(classification, approval, tmp_path / "exports")
    manifest = tmp_path / "exports/glossary-manifest.json"
    result["excluded"] = []
    manifest.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="Missing sources"):
        glossary.reconstruct(manifest, [f"es={result['workbooks']['es']['path']}"], tmp_path / "result", provenance="agent-glossary")
    assert not (tmp_path / "result").exists()


def test_no_approved_english_produces_no_translation_workbooks(tmp_path):
    _, classification, approval = package(tmp_path)
    approval.write_text(json.dumps({"classification_sha256": local_io.file_sha256(classification), "terms": []}))
    result = glossary.export(classification, approval, tmp_path / "exports")
    assert result["workbooks"] == {}
    assert result["records"] == []
    assert len(result["excluded"]) == 3


def test_response_membership_and_probabilities_are_bound_to_request():
    request = glossary.request_for([{"word": "forest", "examples": ["Kavery Forest"]}])
    valid = response(request)
    glossary.validate_response(request, valid)
    valid["answers"]["q0"]["probabilities"]["preserve"] = float("nan")
    with pytest.raises(ValueError, match="probabilities"):
        glossary.validate_response(request, valid)
    with pytest.raises(ValueError, match="membership"):
        glossary.validate_response(request, {**response(request), "answers": {}})


def test_each_jev_question_contains_its_own_candidate_context():
    candidates = [{"word": "forest", "examples": ["Kavery Forest"]}, {"word": "hope", "examples": ["Hope Estate"]}]
    request = glossary.request_for(candidates)
    assert isinstance(request["state"], str)
    for i, candidate in enumerate(candidates):
        assert request["questions"][f"q{i}"]["instructions"]["candidate"] == candidate


def test_output_alias_cannot_overwrite_original(tmp_path):
    original = workbook(tmp_path / "classification.json", ["Forest"])
    before = original.read_bytes()
    with mock.patch.object(glossary, "call_jev") as client:
        with pytest.raises(local_io.TranslationLocalIOError, match="aliases"):
            glossary.classify([f"es={original}"], tmp_path)
        client.assert_not_called()
    assert original.read_bytes() == before


@pytest.mark.parametrize("change", [
    lambda r: r.update(answers=[]),
    lambda r: r["answers"].update(q0=[]),
    lambda r: r["answers"]["q0"].update(type="score"),
    lambda r: r["answers"]["q0"].update(probabilities=[]),
    lambda r: r["answers"]["q0"].update(choice=[]),
    lambda r: r["answers"]["q0"].update(choice="preserve"),
    lambda r: r["answers"]["q0"].update(confidence=float("inf")),
    lambda r: r.update(usage=[]),
    lambda r: r.update(usage={"input_tokens": True}),
])
def test_malformed_provider_shapes_fail_at_the_boundary(change):
    request = glossary.request_for([{"word": "forest", "examples": ["North Forest"]}])
    result = response(request)
    change(result)
    with pytest.raises(ValueError):
        glossary.validate_response(request, result)


def test_corrupted_cache_never_causes_another_paid_call(tmp_path):
    original = workbook(tmp_path / "source.xlsx", ["Forest"])
    directory = tmp_path / "review"
    with mock.patch.object(glossary, "call_jev", side_effect=response):
        glossary.classify([f"es={original}"], directory)
    cache = next((directory / "jev-cache").glob("*.json"))
    payload = json.loads(cache.read_text())
    payload["request"]["model"] = "changed"
    cache.write_text(json.dumps(payload))
    with mock.patch.object(glossary, "call_jev") as client:
        with pytest.raises(ValueError, match="cache does not match"):
            glossary.classify([f"es={original}"], directory)
        client.assert_not_called()


def test_paid_failure_counts_attempt_and_preserves_previous_report(tmp_path):
    original = workbook(tmp_path / "source.xlsx", ["Forest"])
    directory = tmp_path / "review"
    directory.mkdir()
    report = directory / "classification.json"
    report.write_text("previous report")
    with mock.patch.object(glossary, "call_jev", side_effect=ValueError("provider failed")):
        with pytest.raises(ValueError, match="requests_attempted=1"):
            glossary.classify([f"es={original}"], directory)
    assert report.read_text() == "previous report"
    assert not (directory / "jev-cache").exists()


def test_request_budget_stops_new_calls_and_resumes_cached_batch(tmp_path):
    words = ["word" + chr(97 + i // 26) + chr(97 + i % 26) for i in range(129)]
    original = workbook(tmp_path / "source.xlsx", [" ".join(words)])
    directory = tmp_path / "review"
    with mock.patch.object(glossary, "call_jev", side_effect=response) as client:
        with pytest.raises(ValueError, match="Request limit"):
            glossary.classify([f"es={original}"], directory, max_requests=1)
        assert client.call_count == 1
        result = glossary.classify([f"es={original}"], directory, max_requests=1)
    assert client.call_count == 2
    assert result["requests_sent"] == 1
    assert len(result["candidates"]) == 129


def test_manifest_cannot_change_span_identity_to_accept_foreign_workbook(tmp_path):
    _, classification, approval = package(tmp_path)
    result = glossary.export(classification, approval, tmp_path / "exports")
    returned = workbook(tmp_path / "foreign.xlsx", ["Foreign"])
    result["workbooks"]["es"]["shards"] = [{"id": documents.shard_id([localization.source_value_hash("Foreign")])}]
    manifest = tmp_path / "exports/glossary-manifest.json"
    manifest.write_text(json.dumps(result))
    with pytest.raises(ValueError, match="membership does not match reviewed spans"):
        glossary.reconstruct(manifest, [f"es={returned}"], tmp_path / "result", provenance="agent-glossary")
    assert not (tmp_path / "result").exists()


def test_changed_input_during_paid_call_is_not_committed(tmp_path):
    original = workbook(tmp_path / "source.xlsx", ["Forest"])

    def replace_source(request):
        workbook(original, ["Changed"])
        return response(request)

    with mock.patch.object(glossary, "call_jev", side_effect=replace_source):
        with pytest.raises(local_io.TranslationLocalIOError, match="changed while working"):
            glossary.classify([f"es={original}"], tmp_path / "review")
    assert not (tmp_path / "review/classification.json").exists()
    assert not list((tmp_path / "review/jev-cache").glob("*.json"))


def test_http_failure_does_not_log_credentials_or_provider_body():
    request = glossary.request_for([{"word": "forest", "examples": ["North Forest"]}])
    secret = "test-only-key"
    error = urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", 401, secret, {}, io.BytesIO(secret.encode()))
    with mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": secret}):
        with mock.patch.object(glossary.urllib.request, "urlopen", side_effect=error):
            with pytest.raises(ValueError, match="HTTP 401") as caught:
                glossary.call_jev(request)
    assert secret not in str(caught.value)


def test_invalid_paid_response_reports_discarded_token_usage():
    request = glossary.request_for([{"word": "forest", "examples": ["North Forest"]}])
    invalid = response(request)
    invalid["answers"] = {}
    with mock.patch.dict("os.environ", {"TYPESAFE_API_KEY": "test-only-key"}):
        with mock.patch.object(glossary.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(invalid).encode())):
            with pytest.raises(ValueError, match="discarded response input_tokens=42"):
                glossary.call_jev(request)


def test_provider_extra_fields_cannot_change_candidate_identity(tmp_path):
    original = workbook(tmp_path / "source.xlsx", ["Forest"])

    def extra_fields(request):
        result = response(request)
        result["answers"]["q0"].update(word="forged", examples=["forged"], occurrences=100)
        return result

    with mock.patch.object(glossary, "call_jev", side_effect=extra_fields):
        result = glossary.classify([f"es={original}"], tmp_path / "review")
    assert result["candidates"][0]["word"] == "forest"
    assert result["candidates"][0]["examples"] == ["Forest"]
    assert result["candidates"][0]["occurrences"] == 1
