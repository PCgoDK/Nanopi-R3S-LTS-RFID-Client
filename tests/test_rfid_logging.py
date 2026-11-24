import logging
import time

from app.joblog import JobLog
from app.rfid_logging import RFIDJobLogger


class Reader:
    connected = True
    inventory_running = False
    _inventory_owner = None
    _inventory_error = None
    _inventory_response_received = True

    def __init__(self):
        self.starts = 0
        self.stops = 0

    def start_inventory(self, count, owner):
        assert count == 65535
        self._inventory_owner = owner
        self.inventory_running = True
        self.starts += 1

    def stop_inventory(self, owner):
        if self._inventory_owner == owner:
            self._inventory_owner = None
            self.inventory_running = False
            self.stops += 1


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert predicate()


def test_monitor_starts_and_stops_its_own_inventory_and_persists_reads(tmp_path):
    reader = Reader()
    active = {"value": True}
    store = JobLog(tmp_path / "jobs.sqlite3")
    worker = RFIDJobLogger(reader, lambda _: {"active": active["value"]},
                           lambda: {"started_job": "job1", "error": None}, store, logging.getLogger(__name__))
    worker.start()
    try:
        wait_for(lambda: worker.snapshot()["status"] == "running")
        observed_at = time.time()
        worker.observe("ABCD", observed_at)
        worker.observations.join()
        assert store.list_observations()[0]["epc"] == "ABCD"
        active["value"] = False
        wait_for(lambda: worker.snapshot()["status"] == "bridge_off")
        assert not reader.inventory_running
    finally:
        worker.stop()


def test_monitor_does_not_stop_existing_manual_inventory(tmp_path):
    reader = Reader()
    reader.inventory_running = True
    reader._inventory_owner = "manual"
    worker = RFIDJobLogger(reader, lambda _: {"active": True},
                           lambda: {"started_job": "job1", "error": None}, JobLog(tmp_path / "jobs.sqlite3"),
                           logging.getLogger(__name__))
    worker.start()
    try:
        wait_for(lambda: worker.snapshot()["status"] == "busy")
    finally:
        worker.stop()
    assert reader.inventory_running
    assert reader._inventory_owner == "manual"


def test_new_job_starts_and_restarts_inventory_without_stopping_at_job_pause(tmp_path, monkeypatch):
    monkeypatch.setattr("app.rfid_logging.INVENTORY_RESTART_SECONDS", 60)
    reader = Reader()
    capture = {"started_job": None, "error": None}
    worker = RFIDJobLogger(reader, lambda _: {"active": True}, lambda: capture,
                           JobLog(tmp_path / "jobs.sqlite3"), logging.getLogger(__name__))
    worker.start()
    try:
        wait_for(lambda: worker.snapshot()["status"] == "waiting_job")
        assert reader.starts == 0
        capture["started_job"] = "job1"
        wait_for(lambda: reader.starts == 1)
        time.sleep(2.1)
        assert reader.inventory_running
        assert reader.starts == 1
        capture["started_job"] = "job2"
        wait_for(lambda: reader.starts == 2)
        assert reader.stops == 1
    finally:
        worker.stop()


def test_inventory_renews_and_capture_failure_stops_it(tmp_path, monkeypatch):
    monkeypatch.setattr("app.rfid_logging.INVENTORY_RESTART_SECONDS", 0.15)
    reader = Reader()
    capture = {"started_job": "job1", "error": None}
    worker = RFIDJobLogger(reader, lambda _: {"active": True}, lambda: capture,
                           JobLog(tmp_path / "jobs.sqlite3"), logging.getLogger(__name__))
    worker.start()
    try:
        wait_for(lambda: reader.starts >= 2)
        assert reader.stops >= 1
        capture["error"] = "Opsamlingen fejlede."
        wait_for(lambda: worker.snapshot()["status"] == "error")
        assert not reader.inventory_running
    finally:
        worker.stop()
