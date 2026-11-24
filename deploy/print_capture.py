#!/usr/bin/python3
"""Passively collect host-to-printer TCP data while the bridge is running."""

import json
import os
import queue
import select
import signal
import socket
import sqlite3
import struct
import sys
import threading
import time
import uuid
from pathlib import Path

from app.joblog import JobLog
from app.sniffer import ExtractionRule, RuleStore, extract_values


PRINTER = socket.inet_aton("192.168.1.150")
PORT = 9100
INTERFACE = "eth0"
SOCKET_PATH = "/run/nanopi-rfid-capture/capture.sock"
LIMIT = 100000
PAUSE = 2.0
MAX_STREAMS = 16
MAX_PENDING_JOBS = 16


def parse_packet(frame, printer=PRINTER, port=PORT):
    if len(frame) < 14:
        return None
    protocol = struct.unpack_from("!H", frame, 12)[0]
    offset = 14
    while protocol in (0x8100, 0x88A8):
        if len(frame) < offset + 4:
            return None
        protocol = struct.unpack_from("!H", frame, offset + 2)[0]
        offset += 4
    if protocol != 0x0800 or len(frame) < offset + 20:
        return None
    ip = frame[offset:]
    header_len = (ip[0] & 15) * 4
    if ip[0] >> 4 != 4 or header_len < 20 or len(ip) < header_len:
        return None
    length = struct.unpack_from("!H", ip, 2)[0]
    if ip[9] != 6 or ip[16:20] != printer or length < header_len:
        return None
    if struct.unpack_from("!H", ip, 6)[0] & 0x3FFF:
        return {"fragmented": True}
    if len(ip) < length or length < header_len + 20:
        return {"truncated": True}
    tcp = ip[header_len:length]
    src, dst, seq = struct.unpack_from("!HHI", tcp)
    if dst != port:
        return None
    tcp_len = (tcp[12] >> 4) * 4
    if tcp_len < 20 or tcp_len > len(tcp):
        return {"truncated": True}
    return {"key": (ip[12:16], src), "seq": seq, "flags": tcp[13], "data": tcp[tcp_len:]}


class Capture:
    def __init__(self, rule_store, job_log):
        self.lock = threading.Lock()
        self.streams = {}
        self.latest = None
        self.started_job = None
        self.error = None
        self.loss = False
        self.rule_store = rule_store
        self.job_log = job_log
        self.extraction_jobs = queue.Queue(maxsize=MAX_PENDING_JOBS)
        self.extraction_errors = 0

    def packet(self, packet, now):
        if packet is None:
            return
        with self.lock:
            if packet.get("fragmented") or packet.get("truncated"):
                self.mark_loss()
                return
            key = packet["key"]
            seq, flags, data = packet["seq"], packet["flags"], packet["data"]
            stream = self.streams.get(key)
            if flags & 0x02:
                if stream is None or stream["syn"] != seq:
                    if stream and stream["used"]:
                        self.finish(stream)
                    if len(self.streams) >= MAX_STREAMS and key not in self.streams:
                        self.mark_loss()
                        return
                    stream = self.streams[key] = self.new_stream((seq + 1) & 0xFFFFFFFF, seq, now)
                seq = (seq + 1) & 0xFFFFFFFF
            elif stream is None:
                if not data:
                    return
                if len(self.streams) >= MAX_STREAMS:
                    self.mark_loss()
                    return
                stream = self.streams[key] = self.new_stream(seq, None, now)
                stream["uncertain"] = True
            if data:
                offset = (seq - stream["base"]) & 0xFFFFFFFF
                if offset >= 0x80000000:
                    skip = min(len(data), (stream["base"] - seq) & 0xFFFFFFFF)
                    data = data[skip:]
                    offset = 0
                end = offset + len(data)
                if end > LIMIT:
                    stream["truncated"] = True
                if data:
                    stream["last"] = now
                if offset < LIMIT and data:
                    end = min(end, LIMIT)
                    if not stream["used"]:
                        stream["job_id"] = uuid.uuid4().hex
                        self.started_job = stream["job_id"]
                    for index, value in enumerate(data[:end - offset], offset):
                        if stream["present"][index] and stream["data"][index] != value:
                            stream["uncertain"] = True
                        stream["data"][index] = value
                        stream["present"][index] = 1
                    stream["used"] = max(stream["used"], end)
            if flags & (0x01 | 0x04):
                final_offset = (seq + len(data) - stream["base"]) & 0xFFFFFFFF
                if flags & 0x04 or final_offset > stream["used"]:
                    stream["uncertain"] = True
                if stream["used"]:
                    self.finish(stream)
                self.streams.pop(key, None)

    def new_stream(self, base, syn, now):
        return {"base": base, "syn": syn, "data": bytearray(LIMIT),
                "present": bytearray(LIMIT), "used": 0, "last": now,
                "uncertain": self.loss, "truncated": False, "job_id": None}

    def finish(self, stream):
        used = stream["used"]
        first_missing = stream["present"][:used].find(0)
        missing = first_missing >= 0
        raw = bytes(stream["data"][:first_missing if missing else used])
        try:
            text = raw.decode("utf-8")
            invalid = False
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            invalid = True
        self.latest = {"id": stream["job_id"], "text": text,
                       "incomplete": stream["uncertain"] or missing or stream["truncated"],
                       "invalid_utf8": invalid,
                       "extraction": {"state": "pending", "results": None, "error": None}}
        try:
            self.extraction_jobs.put_nowait(("job", self.latest["id"], time.time(), raw, self.latest["incomplete"]))
        except queue.Full:
            self.extraction_errors += 1
            self.latest["extraction"] = {"state": "error", "results": None,
                                         "error": "Udtrækskøen er fuld."}
            print("Automatic extraction queue is full.", file=sys.stderr, flush=True)
        stream["base"] = (stream["base"] + used) & 0xFFFFFFFF
        stream["data"] = bytearray(LIMIT)
        stream["present"] = bytearray(LIMIT)
        stream["used"] = 0
        stream["job_id"] = None
        stream["uncertain"] = self.loss or missing or stream["truncated"]
        stream["truncated"] = False

    def expire(self, now):
        with self.lock:
            for key, stream in list(self.streams.items()):
                if stream["used"] and now - stream["last"] >= PAUSE:
                    self.finish(stream)
                if not stream["used"] and now - stream["last"] >= 120:
                    del self.streams[key]

    def fail(self, message):
        with self.lock:
            self.error = message

    def snapshot(self):
        with self.lock:
            latest = dict(self.latest) if self.latest else None
            if latest is not None:
                latest["extraction_errors"] = self.extraction_errors
            return {"error": self.error, "latest": latest, "packet_loss": self.loss,
                    "started_job": self.started_job}

    def activity(self):
        with self.lock:
            return {"error": self.error, "started_job": self.started_job}

    def mark_loss(self):
        self.loss = True
        for stream in self.streams.values():
            stream["uncertain"] = True
        if self.latest:
            was_complete = not self.latest["incomplete"]
            self.latest["incomplete"] = True
            extraction = self.latest["extraction"]
            if extraction["state"] == "done":
                self.latest["extraction"] = {
                    "state": "done",
                    "results": [{"name": result["name"], "status": "unreliable", "value": None}
                                for result in extraction["results"]],
                    "error": None,
                }
            if was_complete:
                try:
                    self.extraction_jobs.put_nowait(("invalidate", self.latest["id"]))
                except queue.Full:
                    self.extraction_errors += 1
                    print("Could not queue log update for packet loss.", file=sys.stderr, flush=True)

    def process_extractions(self, stopping):
        while not stopping.is_set() or not self.extraction_jobs.empty():
            try:
                job = self.extraction_jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if job[0] == "invalidate":
                    try:
                        self.job_log.mark_incomplete(job[1])
                    except (OSError, sqlite3.Error) as exc:
                        print(f"Could not update incomplete job: {exc}", file=sys.stderr, flush=True)
                        with self.lock:
                            self.extraction_errors += 1
                else:
                    self.process_extraction(job)
            finally:
                self.extraction_jobs.task_done()

    def process_extraction(self, job):
        _, job_id, created_at, raw, incomplete = job
        try:
            rows = self.rule_store.list_existing()
            rules = [ExtractionRule(row["name"], row["marker"], row["length"], row["terminator"])
                     for row in rows]
            if incomplete:
                results = [{"name": rule.name, "status": "unreliable", "value": None}
                           for rule in rules]
            else:
                results = extract_values(raw, rules)
            extraction = {"state": "done", "results": results, "error": None}
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError) as exc:
            print(f"Automatic extraction failed: {exc}", file=sys.stderr, flush=True)
            extraction = {"state": "error", "results": None,
                          "error": "Kunne ikke anvende de gemte udtræksregler."}
            with self.lock:
                self.extraction_errors += 1
        try:
            self.job_log.add_job(job_id, created_at, incomplete, extraction)
        except (OSError, sqlite3.Error) as exc:
            print(f"Could not persist print job: {exc}", file=sys.stderr, flush=True)
            extraction = {"state": "error", "results": None,
                          "error": "Kunne ikke gemme printjobbet i loggen."}
            with self.lock:
                self.extraction_errors += 1
        with self.lock:
            if self.latest and self.latest["id"] == job_id:
                if self.latest["incomplete"] and extraction["state"] == "done":
                    extraction["results"] = [
                        {"name": result["name"], "status": "unreliable", "value": None}
                        for result in extraction["results"]
                    ]
                self.latest["extraction"] = extraction

    def finish_pending(self):
        with self.lock:
            if any(stream["used"] for stream in self.streams.values()):
                self.mark_loss()
                for stream in self.streams.values():
                    if stream["used"]:
                        self.finish(stream)


def serve(capture):
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o660)
        server.listen(8)
        while True:
            client, _ = server.accept()
            with client:
                client.settimeout(3)
                try:
                    readable, _, _ = select.select([client], [], [], 0.02)
                    activity_only = bool(readable and client.recv(1) == b"A")
                    result = capture.activity() if activity_only else capture.snapshot()
                    client.sendall(json.dumps(result, ensure_ascii=True).encode("ascii"))
                except OSError as exc:
                    print(f"Capture response failed: {exc}", file=sys.stderr, flush=True)


def main():
    state_dir = Path(os.environ["RFID_STATE_DIR"])
    with open("/run/nanopi-rfid-bridge/printer.json", encoding="utf-8") as file:
        printer_config = json.load(file)
    printer = socket.inet_aton(printer_config["ip"])
    port = printer_config["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid configured printer port.")
    capture = Capture(RuleStore(state_dir / "sniffer-rules.sqlite3"),
                      JobLog(state_dir / "jobs.sqlite3"))
    stopping = threading.Event()
    worker_stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: stopping.set())
    signal.signal(signal.SIGINT, lambda _signum, _frame: stopping.set())
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)) as packets:
        packets.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
        packets.bind((INTERFACE, 0))
        worker = threading.Thread(target=capture.process_extractions, args=(worker_stopping,), daemon=True)
        worker.start()
        threading.Thread(target=serve, args=(capture,), daemon=True).start()
        while not stopping.is_set():
            ready, _, _ = select.select([packets], [], [], 0.2)
            if ready:
                frame = packets.recv(65535)
                capture.packet(parse_packet(frame, printer, port), time.monotonic())
            capture.expire(time.monotonic())
            stats = packets.getsockopt(263, 6, 8)
            if struct.unpack("II", stats)[1]:
                with capture.lock:
                    capture.mark_loss()
        capture.finish_pending()
        worker_stopping.set()
        worker.join(timeout=10)
        if worker.is_alive():
            print("Capture stopped with unprocessed jobs.", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
