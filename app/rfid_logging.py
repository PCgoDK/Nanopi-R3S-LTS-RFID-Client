import queue
import sqlite3
import threading
import time

import serial


INVENTORY_RESTART_SECONDS = 30
MONITOR_INTERVAL_SECONDS = 0.1


class RFIDJobLogger:
    def __init__(self, reader, bridge_request, capture_request, job_log, logger):
        self.reader = reader
        self.bridge_request = bridge_request
        self.capture_request = capture_request
        self.job_log = job_log
        self.logger = logger
        self.stop_event = threading.Event()
        self.writer_stop = threading.Event()
        self.observations = queue.Queue(maxsize=1024)
        self.lock = threading.Lock()
        self.status = "stopped"
        self.error = None
        self.dropped = 0
        self.storage_error = None
        self.threads = []

    def start(self):
        self.stop_event.clear()
        self.writer_stop.clear()
        self.threads = [
            threading.Thread(target=self._monitor, daemon=True),
            threading.Thread(target=self._store_observations, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        if self.threads:
            self.threads[0].join(timeout=12)
            if self.threads[0].is_alive():
                self.logger.error("RFID monitor did not stop.")
            self.writer_stop.set()
            self.threads[1].join(timeout=12)
            if self.threads[1].is_alive():
                self.logger.error("RFID observation writer did not stop.")

    def observe(self, epc, observed_at):
        try:
            self.observations.put_nowait((epc, observed_at))
        except queue.Full:
            with self.lock:
                self.dropped += 1
                first = self.dropped == 1
            if first:
                self.logger.error("RFID observation queue is full; observations are being lost.")

    def snapshot(self):
        with self.lock:
            return {"status": self.status, "error": self.error, "dropped": self.dropped,
                    "storage_error": self.storage_error}

    def _set_status(self, status, error=None):
        with self.lock:
            changed = self.status != status or self.error != error
            self.status, self.error = status, error
        if changed and error:
            self.logger.warning("RFID job logging: %s", error)

    def _monitor(self):
        next_prune = 0.0
        next_bridge_check = 0.0
        bridge_active = False
        restart_at = None
        current_job = None
        try:
            while not self.stop_event.is_set():
                if time.monotonic() >= next_prune:
                    try:
                        self.job_log.prune()
                    except (OSError, sqlite3.Error) as exc:
                        self.logger.error("Log retention failed: %s", exc)
                        with self.lock:
                            self.storage_error = "Logoprydning fejlede."
                    else:
                        with self.lock:
                            self.storage_error = None
                    next_prune = time.monotonic() + 60
                try:
                    if time.monotonic() >= next_bridge_check:
                        bridge_active = self.bridge_request("status")["active"]
                        next_bridge_check = time.monotonic() + 1
                    if not bridge_active:
                        self.reader.stop_inventory(owner="sniffer")
                        restart_at = None
                        current_job = None
                        self._set_status("bridge_off")
                    elif not self.reader.connected:
                        restart_at = None
                        self._set_status("disconnected")
                    else:
                        capture = self.capture_request()
                        if capture["error"]:
                            raise RuntimeError(capture["error"])
                        started_job = capture["started_job"]
                        if self.reader._inventory_owner == "sniffer" and self.reader._inventory_error:
                            self._set_status("error", self.reader._inventory_error)
                        elif self.reader.inventory_running and self.reader._inventory_owner != "sniffer":
                            restart_at = None
                            self._set_status("busy")
                        elif not started_job:
                            self.reader.stop_inventory(owner="sniffer")
                            restart_at = None
                            current_job = None
                            self._set_status("waiting_job")
                        elif (started_job != current_job or not self.reader.inventory_running or
                              restart_at is None or time.monotonic() >= restart_at):
                            self._set_status("restarting" if current_job else "starting")
                            self.reader.stop_inventory(owner="sniffer")
                            if not self.stop_event.is_set():
                                self.reader.start_inventory(65535, owner="sniffer")
                                current_job = started_job
                                restart_at = time.monotonic() + INVENTORY_RESTART_SECONDS
                        else:
                            self._set_status("running" if self.reader._inventory_response_received else "starting")
                except (OSError, ValueError, KeyError, RuntimeError, serial.SerialException) as exc:
                    self._set_status("error", str(exc))
                    try:
                        self.reader.stop_inventory(owner="sniffer")
                        restart_at = None
                    except (OSError, RuntimeError, serial.SerialException) as stop_exc:
                        self.logger.error("Could not stop automatic inventory: %s", stop_exc)
                self.stop_event.wait(MONITOR_INTERVAL_SECONDS)
        finally:
            try:
                self.reader.stop_inventory(owner="sniffer")
            except (OSError, RuntimeError, serial.SerialException) as exc:
                self._set_status("error", f"Kunne ikke stoppe RFID-læsning: {exc}")

    def _store_observations(self):
        while not self.writer_stop.is_set() or not self.observations.empty():
            try:
                batch = [self.observations.get(timeout=0.5)]
            except queue.Empty:
                continue
            deadline = time.monotonic() + 0.25
            while len(batch) < 100 and not self.writer_stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch.append(self.observations.get(timeout=remaining))
                except queue.Empty:
                    break
            try:
                self.job_log.add_observations(batch)
            except (OSError, sqlite3.Error) as exc:
                with self.lock:
                    self.dropped += len(batch)
                self.logger.error("Could not save an RFID observation: %s", exc)
            finally:
                for _ in batch:
                    self.observations.task_done()
