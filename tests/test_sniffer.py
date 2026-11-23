import pytest

from app.sniffer import ExtractionRule, extract_values


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
