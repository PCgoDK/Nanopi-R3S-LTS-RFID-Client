import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def controller(monkeypatch, tmp_path):
    if "fcntl" not in sys.modules:
        try:
            __import__("fcntl")
        except ModuleNotFoundError:
            monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, flock=lambda *args: None))
    path = Path(__file__).resolve().parents[1] / "deploy" / "bridge_controller.py"
    spec = importlib.util.spec_from_file_location("bridge_controller_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("STATE", "LEASE", "HOOK_ERROR", "TIMER", "LOCK", "PRINTER_CONFIG", "ACTIVE_PRINTER_CONFIG"):
        monkeypatch.setattr(module, name, tmp_path / name.lower())
    return module


def test_preflight_refuses_to_change_an_existing_printer_ipv4(controller, monkeypatch):
    monkeypatch.setattr(controller.shutil, "which", lambda name: name)
    monkeypatch.setattr(controller, "command", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=""))
    monkeypatch.setattr(controller, "interfaces", lambda: {
        "eth0": {"address": "00:11:22:33:44:55", "addr_info": [
            {"family": "inet", "dynamic": True, "local": "192.0.2.10"},
        ]},
        "eth1": {"addr_info": [{"family": "inet", "local": "192.0.2.11"}]},
    })
    monkeypatch.setattr(controller, "nm_device", lambda name: {"managed": True, "uuid": "11111111-1111-1111-1111-111111111111"})

    def command(*args, **kwargs):
        if args[:2] == ("nmcli", "-g"):
            return SimpleNamespace(returncode=0, stdout="auto\nyes\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(controller, "command", command)
    with pytest.raises(RuntimeError, match="printer port already has an IPv4"):
        controller.preflight()


def test_dhcp_renewal_keeps_unchanged_route(controller, monkeypatch):
    controller.store(controller.STATE, {"bridge": "nrtest"})
    controller.store(controller.LEASE, {
        "address": "192.0.2.100", "gateway": "192.0.2.1", "dns": ["192.0.2.1"],
    })
    for key, value in {
        "interface": "nrtest", "ip": "192.0.2.100", "subnet": "255.255.255.0",
        "router": "192.0.2.1", "dns": "192.0.2.1",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(controller, "interfaces", lambda: {
        "nrtest": {"addr_info": [{"family": "inet", "local": "192.0.2.100", "prefixlen": 24}]},
    })
    commands = []
    monkeypatch.setattr(controller, "command", lambda *args, **kwargs: commands.append(args))
    controller.lease_hook("renew")
    assert not any(args[:4] == ("ip", "-4", "route", "add") for args in commands)
    assert ("resolvectl", "dns", "nrtest", "192.0.2.1") in commands
    assert controller.load(controller.LEASE)["address"] == "192.0.2.100"


def test_cleanup_restores_only_owned_resources_and_original_profiles(controller, monkeypatch):
    state = {
        "bridge": "nrtest", "bridge_table": "nrbridge_test", "nat_table": "nrnat_test",
        "wan": {"managed": True, "uuid": "wan-uuid"},
        "lan": {"managed": True, "uuid": "lan-uuid"},
    }
    controller.store(controller.STATE, state)
    monkeypatch.setattr(controller, "nft_tables", lambda: {
        ("bridge", "nrbridge_test"), ("ip", "nrnat_test"), ("ip", "someone_else"),
    })
    monkeypatch.setattr(controller, "interfaces", lambda: {
        "nrtest": {},
        "eth0": {"master": "nrtest", "addr_info": [{"family": "inet", "dynamic": True}]},
        "eth1": {"master": "nrtest"},
    })
    monkeypatch.setattr(controller, "nm_device", lambda name: {"uuid": "wan-uuid"})
    commands = []

    def command(*args, **kwargs):
        commands.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(controller, "command", command)
    controller.cleanup()
    assert ("nft", "delete", "table", "bridge", "nrbridge_test") in commands
    assert ("nft", "delete", "table", "ip", "nrnat_test") in commands
    assert not any("someone_else" in args for args in commands)
    assert ("nmcli", "--wait", "0", "connection", "up", "uuid", "wan-uuid", "ifname", "eth0") in commands
    assert ("nmcli", "--wait", "0", "connection", "up", "uuid", "lan-uuid", "ifname", "eth1") in commands
    assert not controller.STATE.exists()


def test_cleanup_keeps_recovery_state_on_failure(controller, monkeypatch):
    state = {
        "bridge": "nrtest", "bridge_table": "nrbridge_test", "nat_table": "nrnat_test",
        "wan": {"managed": True, "uuid": "wan-uuid"},
        "lan": {"managed": True, "uuid": ""},
    }
    controller.store(controller.STATE, state)
    monkeypatch.setattr(controller, "nft_tables", lambda: {("bridge", "nrbridge_test")})
    monkeypatch.setattr(controller, "interfaces", lambda: {
        "eth0": {"addr_info": [{"family": "inet", "dynamic": True}]}, "eth1": {},
    })
    monkeypatch.setattr(controller, "nm_device", lambda name: {"uuid": "wan-uuid"})

    def command(*args, **kwargs):
        if args[:3] == ("nft", "delete", "table"):
            raise RuntimeError("firewall error")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(controller, "command", command)
    with pytest.raises(RuntimeError, match="firewall error"):
        controller.cleanup()
    assert controller.STATE.exists()


def test_firewall_inspection_failure_does_not_skip_network_restoration(controller, monkeypatch):
    state = {
        "bridge": "nrtest", "bridge_table": "nrbridge_test", "nat_table": "nrnat_test",
        "wan": {"managed": True, "uuid": "wan-uuid"},
        "lan": {"managed": True, "uuid": ""},
    }
    controller.store(controller.STATE, state)
    monkeypatch.setattr(controller, "nft_tables", lambda: (_ for _ in ()).throw(RuntimeError("nft unavailable")))
    monkeypatch.setattr(controller, "interfaces", lambda: {
        "eth0": {"addr_info": [{"family": "inet", "dynamic": True}]}, "eth1": {},
    })
    monkeypatch.setattr(controller, "nm_device", lambda name: {"uuid": "wan-uuid"})
    commands = []
    monkeypatch.setattr(controller, "command", lambda *args, **kwargs: commands.append(args) or SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match="nft unavailable"):
        controller.cleanup()
    assert ("nmcli", "device", "set", "eth0", "managed", "yes") in commands
    assert controller.STATE.exists()


def test_watchdog_is_armed_before_bridge_session(controller, monkeypatch):
    monkeypatch.setattr(controller, "service_state", lambda: "inactive")
    monkeypatch.setattr(controller, "preflight", lambda: {})
    commands = []
    monkeypatch.setattr(controller, "command", lambda *args, **kwargs: commands.append(args) or SimpleNamespace(returncode=0))
    assert controller.handle("start") == {"started": True}
    assert commands[0][0] == "systemd-run"
    assert commands[1] == ("systemctl", "--no-block", "start", controller.SERVICE)
    assert controller.TIMER.exists()


def test_printer_configuration_is_persistent_and_locked_while_bridge_is_active(controller, monkeypatch):
    monkeypatch.setattr(controller, "service_state", lambda: "inactive")
    configured = {"ip": "192.168.1.170", "port": 9200}
    assert controller.handle("configure", configured)["printer"] == configured
    assert controller.printer_config() == configured
    monkeypatch.setattr(controller, "service_state", lambda: "active")
    with pytest.raises(RuntimeError, match="Stop the bridge"):
        controller.handle("configure", {"ip": "192.168.1.171", "port": 9100})
    assert controller.printer_config() == configured


@pytest.mark.parametrize("config", [
    {"ip": "127.0.0.1", "port": 9100},
    {"ip": "192.168.1.1; drop", "port": 9100},
    {"ip": "192.168.1.1", "port": 8080},
    {"ip": "192.168.1.1", "port": True},
])
def test_invalid_printer_configuration_is_rejected(controller, monkeypatch, config):
    monkeypatch.setattr(controller, "service_state", lambda: "inactive")
    with pytest.raises(ValueError):
        controller.handle("configure", config)


def test_firewall_uses_configured_printer_address(controller, monkeypatch):
    calls = []
    monkeypatch.setattr(controller, "command", lambda *args, **kwargs: calls.append(kwargs["input_text"]))
    controller.apply_firewall({"bridge_table": "bt", "nat_table": "nt", "bridge": "nrtest",
                               "mac": "00:11:22:33:44:55",
                               "printer": {"ip": "192.168.1.170", "port": 9200}})
    assert calls[0].count("ip daddr 192.168.1.170 tcp dport 8080") == 2
    assert "192.168.1.150" not in calls[0]


def test_stopping_bridge_is_not_reported_as_failed_recovery(controller, monkeypatch):
    controller.store(controller.STATE, {"bridge": "nrtest"})
    monkeypatch.setattr(controller, "service_state", lambda: "deactivating")
    status = controller.handle("status")
    assert status["transitioning"] is True
    assert status["needs_recovery"] is False
    assert status["active"] is False


def test_confirmation_requires_a_ready_lease_and_cancels_the_watchdog(controller, monkeypatch):
    controller.store(controller.TIMER, {"unit": "test-watchdog"})
    monkeypatch.setattr(controller, "service_active", lambda: True)
    commands = []
    monkeypatch.setattr(controller, "command", lambda *args, **kwargs: commands.append(args))
    with pytest.raises(RuntimeError, match="awaiting confirmation"):
        controller.handle("confirm")
    controller.store(controller.LEASE, {"address": "192.0.2.100"})
    assert controller.handle("confirm") == {"confirmed": True}
    assert commands == [("systemctl", "stop", "test-watchdog.timer")]
    assert not controller.TIMER.exists()
