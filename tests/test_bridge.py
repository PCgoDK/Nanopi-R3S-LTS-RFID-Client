import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import bridge, main


def request(host="192.168.1.132:8080", origin=None, content_type="application/json"):
    headers = {"host": host, "content-type": content_type}
    if origin is not None:
        headers["origin"] = origin
    return SimpleNamespace(headers=headers, url=SimpleNamespace(scheme="http"))


@pytest.mark.parametrize("action", ["start", "stop"])
def test_bridge_action_accepts_same_origin(monkeypatch, action):
    calls = []
    monkeypatch.setattr(main, "bridge_request", lambda value: calls.append(value) or {"ok": True})
    result = main.change_bridge(action, request(origin="http://192.168.1.132:8080"))
    assert result == {"ok": True}
    assert calls == [action]


@pytest.mark.parametrize("origin", ["http://attacker.example", "https://192.168.1.132:8080", "http://[invalid"])
def test_bridge_action_rejects_cross_origin(monkeypatch, origin):
    monkeypatch.setattr(main, "bridge_request", lambda value: pytest.fail("Controller must not be called"))
    with pytest.raises(HTTPException) as error:
        main.change_bridge("start", request(origin=origin))
    assert error.value.status_code == 403


def test_bridge_action_rejects_form_posts(monkeypatch):
    monkeypatch.setattr(main, "bridge_request", lambda value: pytest.fail("Controller must not be called"))
    with pytest.raises(HTTPException) as error:
        main.change_bridge("start", request(content_type="application/x-www-form-urlencoded"))
    assert error.value.status_code == 415


def test_bridge_confirmation_requires_printer_address(monkeypatch):
    monkeypatch.setattr(main, "bridge_request", lambda value: (
        {"printer": {"ip": "192.168.1.150", "port": 9100}} if value == "status" else {"action": value}
    ))
    with pytest.raises(HTTPException) as error:
        main.change_bridge("confirm", request())
    assert error.value.status_code == 403
    assert main.change_bridge(
        "confirm", request(host="192.168.1.150:8080", origin="http://192.168.1.150:8080")
    ) == {"action": "confirm"}


def test_bridge_controller_errors_are_explicit(monkeypatch):
    def fail(_action):
        raise OSError("controller unavailable")

    monkeypatch.setattr(main, "bridge_request", fail)
    with pytest.raises(HTTPException) as error:
        main.get_bridge_status()
    assert error.value.status_code == 503
    assert "controller unavailable" in error.value.detail


def test_socket_response_can_arrive_in_multiple_chunks(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.chunks = [b'{"ok":true,', b'"result":{"active":false}}', b""]
            self.request = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, _path):
            pass

        def sendall(self, value):
            self.request = json.loads(value)

        def shutdown(self, _how):
            pass

        def recv(self, _size):
            return self.chunks.pop(0)

    fake = FakeSocket()
    monkeypatch.setattr(bridge.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(bridge.socket, "socket", lambda *args: fake)
    assert bridge.bridge_request("status") == {"active": False}
    assert fake.request == {"action": "status"}
