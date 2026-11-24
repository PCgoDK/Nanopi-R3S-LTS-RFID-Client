import socket
import struct
import threading

from deploy.print_capture import MAX_PENDING_JOBS, Capture, parse_packet
from app.joblog import JobLog
from app.sniffer import ExtractionRule, RuleStore


def frame(seq, data=b"", flags=0x18, port=9100, fragment=0):
    tcp = struct.pack("!HHIIBBHHH", 1234, port, seq, 0, 0x50, flags, 0, 0, 0) + data
    ip = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, 20 + len(tcp), 1, fragment, 64, 6, 0,
        socket.inet_aton("192.168.1.10"), socket.inet_aton("192.168.1.150"),
    )
    return b"\x00" * 12 + b"\x08\x00" + ip + tcp


def test_out_of_order_and_retransmission_produce_one_ordered_job(tmp_path):
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(100, flags=0x02)), 0)
    capture.packet(parse_packet(frame(104, b"DEF")), 1)
    capture.packet(parse_packet(frame(101, b"ABC")), 1.1)
    capture.packet(parse_packet(frame(101, b"ABC")), 1.2)
    capture.expire(3.3)
    latest = capture.snapshot()["latest"]
    assert latest["text"] == "ABCDEF"
    assert not latest["incomplete"]
    capture.packet(parse_packet(frame(101, b"ABC")), 4)
    capture.expire(7)
    assert capture.snapshot()["latest"]["id"] == latest["id"]


def test_gap_and_midstream_capture_are_marked_incomplete(tmp_path):
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(50, flags=0x02)), 0)
    capture.packet(parse_packet(frame(51, b"AB")), 1)
    capture.packet(parse_packet(frame(55, b"EF")), 1)
    capture.expire(4)
    assert capture.latest["text"] == "AB"
    assert capture.latest["incomplete"]
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(200, b"partial")), 0)
    capture.expire(3)
    assert capture.latest["incomplete"]


def test_finish_on_fin_and_report_binary_and_missing_tail(tmp_path):
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(10, flags=0x02)), 0)
    capture.packet(parse_packet(frame(11, b"A\xff")), 1)
    capture.packet(parse_packet(frame(16, flags=0x11)), 1.1)
    assert capture.latest["text"] == "A\ufffd"
    assert capture.latest["incomplete"]
    assert capture.latest["invalid_utf8"]


def test_filter_and_report_ip_fragments(tmp_path):
    assert parse_packet(frame(1, b"x", port=8080)) is None
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(1, b"x", fragment=0x2000)), 0)
    assert capture.snapshot()["packet_loss"]


def test_job_start_is_visible_before_job_finishes_and_changes_for_next_job(tmp_path):
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(100, flags=0x02)), 0)
    assert capture.activity()["started_job"] is None
    capture.packet(parse_packet(frame(101, b"ABC")), 0.1)
    first = capture.activity()["started_job"]
    assert first and capture.snapshot()["latest"] is None
    capture.packet(parse_packet(frame(101, b"ABC")), 0.2)
    assert capture.activity()["started_job"] == first
    capture.expire(2.2)
    assert capture.snapshot()["latest"]["id"] == first
    capture.packet(parse_packet(frame(104, b"DEF")), 2.3)
    assert capture.activity()["started_job"] != first


def test_packet_filter_uses_configured_printer_and_port():
    printer = socket.inet_aton("192.168.1.150")
    assert parse_packet(frame(1, b"x", port=9200), printer, 9200) is not None
    assert parse_packet(frame(1, b"x", port=9100), printer, 9200) is None
    assert parse_packet(frame(1, b"x"), socket.inet_aton("192.168.1.151"), 9100) is None


def test_automatic_extraction_uses_raw_bytes_and_saved_rules(tmp_path):
    store = RuleStore(tmp_path / "rules.sqlite3")
    store.save(ExtractionRule("Order", "^FD", terminator="^FS"))
    capture = Capture(store, JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(100, flags=0x02)), 0)
    capture.packet(parse_packet(frame(101, b"\xff^FD136556^FS", flags=0x19)), 1)

    assert capture.latest["extraction"]["state"] == "pending"
    capture.process_extraction(capture.extraction_jobs.get_nowait())
    assert capture.latest["invalid_utf8"]
    assert capture.latest["extraction"] == {
        "state": "done", "results": [{"name": "Order", "status": "matched", "value": "136556"}],
        "error": None,
    }


def test_automatic_extraction_does_not_claim_matches_for_incomplete_capture(tmp_path):
    store = RuleStore(tmp_path / "rules.sqlite3")
    store.save(ExtractionRule("Order", "^FD", terminator="^FS"))
    capture = Capture(store, JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(100, b"^FD136556^FS")), 0)
    capture.expire(3)
    capture.process_extraction(capture.extraction_jobs.get_nowait())
    assert capture.latest["extraction"]["results"] == [
        {"name": "Order", "status": "unreliable", "value": None},
    ]


def test_automatic_extraction_reports_storage_failure_and_preserves_latest_job(tmp_path):
    database = tmp_path / "rules.sqlite3"
    database.write_text("invalid database")
    capture = Capture(RuleStore(database), JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(1, flags=0x02)), 0)
    capture.packet(parse_packet(frame(2, b"first", flags=0x19)), 1)
    first = capture.extraction_jobs.get_nowait()
    capture.packet(parse_packet(frame(100, flags=0x02)), 2)
    capture.packet(parse_packet(frame(101, b"second", flags=0x19)), 3)
    capture.process_extraction(first)
    assert capture.latest["text"] == "second"
    assert capture.latest["extraction"]["state"] == "pending"
    assert capture.snapshot()["latest"]["extraction_errors"] == 1
    capture.process_extraction(capture.extraction_jobs.get_nowait())
    assert capture.latest["extraction"]["state"] == "error"
    assert capture.latest["extraction"]["results"] is None


def test_missing_rule_database_means_no_rules(tmp_path):
    assert RuleStore(tmp_path / "rules.sqlite3").list_existing() == []


def test_packet_loss_after_extraction_invalidates_reported_matches(tmp_path):
    store = RuleStore(tmp_path / "rules.sqlite3")
    store.save(ExtractionRule("Order", "^FD", terminator="^FS"))
    capture = Capture(store, JobLog(tmp_path / "jobs.sqlite3"))
    capture.packet(parse_packet(frame(100, flags=0x02)), 0)
    capture.packet(parse_packet(frame(101, b"^FD123^FS", flags=0x19)), 1)
    capture.process_extraction(capture.extraction_jobs.get_nowait())
    assert capture.latest["extraction"]["results"][0]["status"] == "matched"
    capture.packet(parse_packet(frame(1, b"x", fragment=0x2000)), 2)
    assert capture.latest["extraction"]["results"] == [
        {"name": "Order", "status": "unreliable", "value": None},
    ]


def test_full_extraction_queue_reports_failure(tmp_path):
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), JobLog(tmp_path / "jobs.sqlite3"))
    for index in range(MAX_PENDING_JOBS + 1):
        seq = index * 10
        capture.packet(parse_packet(frame(seq, flags=0x02)), index)
        capture.packet(parse_packet(frame(seq + 1, b"x", flags=0x19)), index)
    assert capture.latest["extraction"]["state"] == "error"
    assert capture.snapshot()["latest"]["extraction_errors"] == 1


def test_stopping_finishes_partial_job_as_uncertain_and_flushes_to_log(tmp_path):
    log = JobLog(tmp_path / "jobs.sqlite3")
    capture = Capture(RuleStore(tmp_path / "rules.sqlite3"), log)
    capture.packet(parse_packet(frame(100, flags=0x02)), 0)
    capture.packet(parse_packet(frame(101, b"partial")), 1)
    capture.finish_pending()
    stopping = threading.Event()
    worker = threading.Thread(target=capture.process_extractions, args=(stopping,))
    worker.start()
    capture.extraction_jobs.join()
    stopping.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert log.list_jobs()[0]["incomplete"]
