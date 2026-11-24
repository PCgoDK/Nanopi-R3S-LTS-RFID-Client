import csv
import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main
from app.joblog import JobLog, RETENTION_SECONDS


def job(store, job_id, when, incomplete=False):
    store.add_job(job_id, when, incomplete, {
        "state": "done", "results": [{"name": "Order", "status": "matched", "value": "123"}], "error": None,
    })


def test_jobs_observations_and_manual_multiple_links_survive_reopening(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    store = JobLog(path)
    job(store, "job", 100)
    first = store.add_observation("ABC123", 101)
    assert store.add_observation("ABC123", 101.2) == first
    second = store.add_observation("ABC123", 102)
    assert second != first
    assert store.link("job", first)
    assert store.link("job", second)
    restored = JobLog(path)
    assert len(restored.list_jobs()[0]["epcs"]) == 2
    assert restored.list_jobs()[0]["resolution"] == "linked"
    assert restored.list_observations()[1]["reads"] == 2
    assert restored.unlink("job", first)
    assert restored.unlink("job", second)
    assert restored.list_jobs()[0]["resolution"] == "pending"


def test_no_tag_requires_no_links_and_missing_references_are_rejected(tmp_path):
    store = JobLog(tmp_path / "jobs.sqlite3")
    job(store, "job", 100)
    observation = store.add_observation("ABC", 101)
    assert not store.link("missing", observation)
    assert not store.link("job", 999)
    assert store.link("job", observation)
    with pytest.raises(ValueError):
        store.mark_no_tag("job")
    assert store.unlink("job", observation)
    assert store.mark_no_tag("job")
    assert store.list_jobs()[0]["resolution"] == "no_tag"
    assert store.clear_no_tag("job")
    assert store.list_jobs()[0]["resolution"] == "pending"


def test_expiration_and_csv_keep_confirmed_links_without_automatic_assumptions(tmp_path):
    store = JobLog(tmp_path / "jobs.sqlite3")
    now = RETENTION_SECONDS + 100
    job(store, "old", 99)
    job(store, "new", 101)
    first = store.add_observation("ABC", 99)
    second = store.add_observation("DEF", 102)
    assert store.link("new", first)
    assert store.link("new", second)
    store.prune(now)
    assert [item["id"] for item in store.list_jobs()] == ["new"]
    assert [item["epc"] for item in store.list_observations()] == ["DEF"]
    rows = list(csv.DictReader(io.StringIO("".join(store.csv_rows()).lstrip("\ufeff"))))
    assert [row["epc"] for row in rows] == ["ABC", "DEF"]


def test_incomplete_capture_masks_extracted_values_in_log_and_csv(tmp_path):
    store = JobLog(tmp_path / "jobs.sqlite3")
    job(store, "job", 100)
    store.mark_incomplete("job")
    assert store.list_jobs()[0]["results"] == [
        {"name": "Order", "status": "unreliable", "value": None},
    ]
    assert "123" not in "".join(store.csv_rows())


def test_log_api_requires_same_origin_for_manual_assignments(tmp_path, monkeypatch):
    store = JobLog(tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(main, "job_log", store)
    job(store, "job", 100)
    observation = store.add_observation("ABC", 101)
    payload = main.SnifferLinkRequest(observation_id=observation)
    request = SimpleNamespace(
        headers={"host": "example.test", "origin": "http://attacker.test", "content-type": "application/json"},
        url=SimpleNamespace(scheme="http"),
    )
    with pytest.raises(HTTPException) as error:
        main.link_job_epc("job", payload, request)
    assert error.value.status_code == 403
    assert store.list_jobs()[0]["epcs"] == []
    request.headers["origin"] = "http://example.test"
    assert main.link_job_epc("job", payload, request) == {"linked": True}
    assert store.list_jobs()[0]["epcs"][0]["epc"] == "ABC"
