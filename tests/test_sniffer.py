import pytest
from fastapi import HTTPException

from app import main
from app.sniffer import ExtractionRule, RuleStore, extract_values


def test_extracts_first_match_and_unicode_characters_in_print_data():
    rule = ExtractionRule("Order", "^FD", 3)
    data = b"\xff\x00^FD" + "Æ12".encode("utf-8") + b"^FS^FD999"

    assert extract_values(data, [rule]) == [{"name": "Order", "status": "matched", "value": "Æ12"}]


def test_reports_missing_truncated_and_non_utf8_values():
    data = b"start:abc\xff"
    rules = [
        ExtractionRule("missing", "other", 2),
        ExtractionRule("short", "start:", 5),
        ExtractionRule("binary", "start:", 4),
    ]

    assert extract_values(data, rules) == [
        {"name": "missing", "status": "not_found", "value": None},
        {"name": "short", "status": "incomplete", "value": None},
        {"name": "binary", "status": "invalid_utf8", "value": None},
    ]


def test_extracts_up_to_first_terminator_without_including_it():
    rule = ExtractionRule("Order", "^FD", terminator="^FS")
    data = b"\xff^FD136556^FS^FDOTHER^FS"

    assert extract_values(data, [rule]) == [{"name": "Order", "status": "matched", "value": "136556"}]


def test_terminator_handles_empty_missing_and_invalid_values():
    rule = ExtractionRule("Order", "^FD", terminator="^FS")

    assert extract_values(b"^FD^FS", [rule])[0] == {"name": "Order", "status": "matched", "value": ""}
    assert extract_values(b"^FD123^F", [rule])[0]["status"] == "incomplete"
    assert extract_values(b"^FD12\xff^FS", [rule])[0]["status"] == "invalid_utf8"


@pytest.mark.parametrize(
    ("name", "marker", "length"),
    [("", "^FD", 1), ("x", "", 1), ("x", "^FD", 0), ("x", "^FD", True)],
)
def test_rejects_invalid_extraction_rules(name, marker, length):
    with pytest.raises(ValueError):
        ExtractionRule(name, marker, length)


@pytest.mark.parametrize(
    ("length", "terminator"),
    [(None, None), (6, "^FS"), (None, ""), (None, 42)],
)
def test_requires_exactly_one_valid_end_condition(length, terminator):
    with pytest.raises(ValueError):
        ExtractionRule("Order", "^FD", length, terminator)


def test_rules_persist_and_preview_multiple_end_conditions(tmp_path, monkeypatch):
    database = tmp_path / "sniffer" / "rules.sqlite3"
    monkeypatch.setattr(main, "sniffer_rules", RuleStore(database))
    first = main.create_sniffer_rule(main.SnifferRuleRequest(name="Order", marker="^FD", terminator="^FS"))
    main.create_sniffer_rule(main.SnifferRuleRequest(name="Prefix", marker="^FD", length=3))

    monkeypatch.setattr(main, "sniffer_rules", RuleStore(database))
    assert [rule["name"] for rule in main.get_sniffer_rules()["rules"]] == ["Order", "Prefix"]
    assert main.preview_sniffer_rules(main.SnifferPreviewRequest(text="^FDÆ123^FS")) == {
        "results": [
            {"name": "Order", "status": "matched", "value": "Æ123"},
            {"name": "Prefix", "status": "matched", "value": "Æ12"},
        ]
    }

    main.update_sniffer_rule(first["id"], main.SnifferRuleRequest(
        name="Order", marker="^FO", length=2, version=first["version"],
    ))
    assert main.preview_sniffer_rules(main.SnifferPreviewRequest(text="^FDX^FS"))["results"][0]["status"] == "not_found"
    assert main.delete_sniffer_rule(first["id"], 2) == {"deleted": True}
    assert len(RuleStore(database).list()) == 1


def test_conflicts_and_invalid_rules_do_not_overwrite_data(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "sniffer_rules", RuleStore(tmp_path / "rules.sqlite3"))
    first = main.create_sniffer_rule(main.SnifferRuleRequest(name="Order", marker="^FD", length=2))
    with pytest.raises(HTTPException) as duplicate:
        main.create_sniffer_rule(main.SnifferRuleRequest(name="Order", marker="^FD", length=2))
    assert duplicate.value.status_code == 409
    with pytest.raises(HTTPException) as invalid:
        main.create_sniffer_rule(main.SnifferRuleRequest(name="Bad", marker="^FD", length=2, terminator="^FS"))
    assert invalid.value.status_code == 422

    request = main.SnifferRuleRequest(name="Order", marker="new", length=1, version=first["version"])
    main.update_sniffer_rule(first["id"], request)
    with pytest.raises(HTTPException) as conflict:
        main.update_sniffer_rule(first["id"], request)
    assert conflict.value.status_code == 409
    with pytest.raises(HTTPException) as delete_conflict:
        main.delete_sniffer_rule(first["id"], first["version"])
    assert delete_conflict.value.status_code == 409
    assert main.get_sniffer_rules()["rules"][0]["marker"] == "new"


def test_storage_failure_is_reported(tmp_path, monkeypatch):
    parent = tmp_path / "not-a-directory"
    parent.write_text("x")
    monkeypatch.setattr(main, "sniffer_rules", RuleStore(parent / "rules.sqlite3"))
    with pytest.raises(HTTPException) as error:
        main.get_sniffer_rules()
    assert error.value.status_code == 503


def test_capture_status_is_exposed_and_unavailable_capture_is_reported(monkeypatch):
    status = {"error": None, "latest": {"id": "job", "text": "^XA^XZ"}, "packet_loss": False}
    monkeypatch.setattr(main, "capture_status", lambda: status)
    assert main.get_capture_status() == status

    def fail():
        raise OSError("capture unavailable")

    monkeypatch.setattr(main, "capture_status", fail)
    with pytest.raises(HTTPException) as error:
        main.get_capture_status()
    assert error.value.status_code == 503
