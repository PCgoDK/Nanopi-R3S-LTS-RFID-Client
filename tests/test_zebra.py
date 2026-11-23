import io
import json
from urllib.error import HTTPError, URLError

import pytest

from app.zebra import ZebraDeliveryError, send_tag_read


class Response(io.BytesIO):
    status = 201


def test_sends_epc_and_extracted_values_via_authenticated_rest(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return Response(b'{"id":42,"reader_id":3,"epc_hex":"AB12"}')

    monkeypatch.setattr("app.zebra.build_opener", lambda *handlers: Opener())
    result = send_tag_read(
        "https://192.168.1.151", "test-token", 3, "ab 12",
        "job-1", "2026-01-01T10:42:09Z", {"Order": "136556"},
    )

    request, timeout = calls[0]
    payload = json.loads(request.data)
    assert result == 42
    assert request.full_url == "https://192.168.1.151/api/v1/tag-reads"
    assert request.get_header("Authorization") == "Bearer test-token"
    assert timeout == 5
    assert payload["reader_id"] == 3
    assert payload["epc_hex"] == "AB12"
    assert json.loads(payload["raw_payload"]) == {
        "job_id": "job-1", "timestamp": "2026-01-01T10:42:09Z", "values": {"Order": "136556"}
    }


@pytest.mark.parametrize(
    ("url", "epc"),
    [("http://192.168.1.151", "AB12"), ("https://user:pass@server", "AB12"),
     ("https://server", "NoTag"), ("https://server", "ABC")],
)
def test_rejects_unsafe_address_and_invalid_epc(url, epc):
    with pytest.raises(ValueError):
        send_tag_read(url, "test-token", 1, epc, "job", "time", {})


def test_reports_rejected_and_unconfirmed_delivery(monkeypatch):
    class Opener:
        def __init__(self, error):
            self.error = error

        def open(self, request, timeout):
            raise self.error

    errors = [
        (HTTPError("https://server", 401, "Unauthorized", {}, None), "HTTP 401"),
        (HTTPError("https://server", 500, "Internal Server Error", {}, None), "may have been stored"),
        (URLError("connection lost"), "may have received"),
    ]
    for error, message in errors:
        monkeypatch.setattr("app.zebra.build_opener", lambda *handlers: Opener(error))
        with pytest.raises(ZebraDeliveryError, match=message):
            send_tag_read("https://server", "test-token", 1, "AB12", "job", "time", {})


def test_does_not_accept_success_without_matching_confirmation(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            return Response(b'{"id":42,"reader_id":3,"epc_hex":"CD34"}')

    monkeypatch.setattr("app.zebra.build_opener", lambda *handlers: Opener())
    with pytest.raises(ZebraDeliveryError, match="may have been stored"):
        send_tag_read("https://server", "test-token", 3, "AB12", "job", "time", {})
