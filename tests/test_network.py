import json
import subprocess

import pytest
from fastapi import HTTPException

from app.main import get_network


def test_returns_only_wan_dhcp_address(monkeypatch):
    def run(command, **kwargs):
        assert command == ["ip", "-j", "-4", "address", "show", "dev", "eth0"]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps([{
            "ifname": "eth0",
            "addr_info": [
                {"family": "inet", "local": "192.168.1.200"},
                {"family": "inet", "local": "192.168.1.132", "dynamic": True},
            ],
        }]))

    monkeypatch.setattr("app.main.subprocess.run", run)
    assert get_network() == {"wan_ip": "192.168.1.132"}


def test_reports_absent_dynamic_address(monkeypatch):
    monkeypatch.setattr(
        "app.main.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=json.dumps([{
            "ifname": "eth0",
            "addr_info": [{"family": "inet", "local": "192.168.1.200"}],
        }])),
    )
    assert get_network() == {"wan_ip": None}


@pytest.mark.parametrize("output", [
    "not json",
    json.dumps([{"ifname": "eth0", "addr_info": [{"family": "inet", "dynamic": True, "local": "invalid"}]}]),
    json.dumps([{"ifname": "eth0", "addr_info": [
        {"family": "inet", "dynamic": True, "local": "192.168.1.1"},
        {"family": "inet", "dynamic": True, "local": "192.168.1.2"},
    ]}]),
])
def test_rejects_invalid_or_ambiguous_addresses(monkeypatch, output):
    monkeypatch.setattr(
        "app.main.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout=output),
    )
    with pytest.raises(HTTPException) as error:
        get_network()
    assert error.value.status_code == 503


def test_reports_network_lookup_failures(monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 5)

    monkeypatch.setattr("app.main.subprocess.run", run)
    with pytest.raises(HTTPException) as error:
        get_network()
    assert error.value.status_code == 503
