"""A random execution failure cannot count as the monitoring negative control."""

import copy
import json

import pytest

from scripts.verify_alert_probe import PROBES, main, verify_probe


def evidence(job="wdpa-monthly"):
    flag, value, marker = PROBES[job]
    name = job + "-abc12"
    execution = {"metadata": {"name": name}, "status": {"completionTime": "2026-10-05T23:00:00Z", "failedCount": 1},
                 "spec": {"template": {"spec": {"containers": [{"env": [{"name": flag, "value": value}]}]}}}}
    logs = [{"labels": {"run.googleapis.com/execution_name": name},
             "resource": {"labels": {"job_name": job}}, "textPayload": "RuntimeError: " + marker}]
    return execution, logs, name


@pytest.mark.parametrize("job", sorted(PROBES))
def test_negative_control_and_delivery_are_distinct(job):
    execution, logs, name = evidence(job)
    report = verify_probe(execution, logs, job=job, execution_name=name)
    assert report["negative_control"] == "verified"
    assert report["delivery"] == "pending"
    report = verify_probe(execution, logs, job=job, execution_name=name,
                          delivery_confirmation_url="https://skytruth.slack.com/archives/C123/p1759708800000000")
    assert report["delivery"] == "confirmed"


@pytest.mark.parametrize("failure", ["pending", "succeeded", "cancelled", "no-override", "wrong-override", "unrelated-log", "unrelated-failure", "wrong-job"])
def test_other_failures_cannot_be_accepted_as_controlled(failure):
    execution, logs, name = evidence()
    execution, logs = copy.deepcopy(execution), copy.deepcopy(logs)
    if failure == "pending":
        del execution["status"]["completionTime"]
    elif failure == "succeeded":
        execution["status"]["succeededCount"] = 1
    elif failure == "cancelled":
        execution["status"]["cancelledCount"] = 1
    elif failure == "no-override":
        execution["spec"]["template"]["spec"]["containers"][0]["env"] = []
    elif failure == "wrong-override":
        execution["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "false"
    elif failure == "unrelated-log":
        logs[0]["labels"]["run.googleapis.com/execution_name"] = "wdpa-monthly-other"
    elif failure == "unrelated-failure":
        logs[0]["textPayload"] = "Memory limit exceeded"
    elif failure == "wrong-job":
        name = "other-job-abc12"
    with pytest.raises(ValueError):
        verify_probe(execution, logs, job="wdpa-monthly", execution_name=name)


@pytest.mark.parametrize("url", ["https://example.com/probe", "https://skytruth.slack.com", "verified", "https://evil.slack.com.example.com/archives/C123/p1759708800000000"])
def test_confirmation_requires_a_message_permalink(url):
    execution, logs, name = evidence()
    with pytest.raises(ValueError):
        verify_probe(execution, logs, job="wdpa-monthly", execution_name=name, delivery_confirmation_url=url)


def test_delivery_verification_cannot_pass_with_only_a_negative_control(tmp_path, monkeypatch):
    execution, logs, name = evidence()
    execution_path, logs_path, output = tmp_path / "execution.json", tmp_path / "logs.json", tmp_path / "report.json"
    execution_path.write_text(json.dumps(execution))
    logs_path.write_text(json.dumps(logs))
    monkeypatch.setattr("sys.argv", ["probe", "--execution", str(execution_path), "--logs", str(logs_path),
                                    "--job", "wdpa-monthly", "--execution-name", name,
                                    "--require-delivery", "--output", str(output)])
    with pytest.raises(ValueError, match="confirmation remains a prerequisite"):
        main()
    assert not output.exists()
