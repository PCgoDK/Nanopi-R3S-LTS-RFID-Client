#!/usr/bin/python3
"""Privileged bridge controller. The bridge is never enabled at boot."""

import fcntl
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path


RUNTIME = Path("/run/nanopi-rfid-bridge")
SOCKET = RUNTIME / "control.sock"
STATE = RUNTIME / "state.json"
LEASE = RUNTIME / "lease.json"
HOOK_ERROR = RUNTIME / "dhcp-error"
TIMER = RUNTIME / "watchdog"
LOCK = RUNTIME / "lock"
SERVICE = "nanopi-rfid-bridge-session.service"
WAN = "eth0"
LAN = "eth1"
PRINTER = "192.168.1.150"
PRINTER_CONFIG = Path("/etc/nanopi-rfid-client/printer.json")
ACTIVE_PRINTER_CONFIG = RUNTIME / "printer.json"
ROUTE_METRIC = 32765
PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


def command(*args, timeout=15, check=True, input_text=None):
    result = subprocess.run(
        list(args), input=input_text, capture_output=True, text=True,
        timeout=timeout, check=False,
        env={"PATH": PATH, "LC_ALL": "C"},
    )
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result


def store(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_printer(value):
    if not isinstance(value, dict) or set(value) != {"ip", "port"}:
        raise ValueError("Invalid printer configuration.")
    address = ipaddress.IPv4Address(value["ip"])
    port = value["port"]
    if (address.is_multicast or address.is_loopback or address.is_unspecified or
            str(address) == "255.255.255.255"):
        raise ValueError("Printer address must be a unicast IPv4 address.")
    if type(port) is not int or not 1 <= port <= 65535 or port == 8080:
        raise ValueError("Printer port must be 1–65535 and cannot be 8080.")
    return {"ip": str(address), "port": port}


def printer_config():
    if not PRINTER_CONFIG.exists():
        return {"ip": PRINTER, "port": 9100}
    return validate_printer(load(PRINTER_CONFIG))


def interfaces():
    data = json.loads(command("ip", "-j", "address", "show").stdout)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected interface information.")
    return {item["ifname"]: item for item in data}


def nm_device(name):
    output = command(
        "nmcli", "-g", "GENERAL.NM-MANAGED,GENERAL.CON-UUID",
        "device", "show", name,
    ).stdout.splitlines()
    if len(output) != 2 or output[0] not in {"yes", "no"}:
        raise RuntimeError(f"Cannot determine the NetworkManager state of {name}.")
    if output[1] and not re.fullmatch(r"[0-9a-fA-F-]{36}", output[1]):
        raise RuntimeError(f"Unexpected NetworkManager connection for {name}.")
    return {"managed": output[0] == "yes", "uuid": output[1]}


def nft_tables():
    data = json.loads(command("nft", "-j", "list", "tables").stdout)
    if not isinstance(data, dict) or not isinstance(data.get("nftables"), list):
        raise RuntimeError("Unexpected nftables table list.")
    tables = set()
    for entry in data["nftables"]:
        table = entry.get("table")
        if table:
            tables.add((table["family"], table["name"]))
    return tables


def preflight():
    for binary in ("ip", "nmcli", "nft", "busybox", "resolvectl", "systemctl", "systemd-run", "ss"):
        if shutil.which(binary) is None:
            raise RuntimeError(f"Required command not installed: {binary}.")
    if command("systemctl", "is-active", "--quiet", "NetworkManager", check=False).returncode:
        raise RuntimeError("NetworkManager is not active.")
    if command("systemctl", "is-active", "--quiet", "systemd-resolved", check=False).returncode:
        raise RuntimeError("systemd-resolved is not active.")
    links = interfaces()
    if WAN not in links or LAN not in links:
        raise RuntimeError("The expected ports are missing.")
    for name in (WAN, LAN):
        if links[name].get("master"):
            raise RuntimeError(f"{name} already belongs to a master device.")
    wan = nm_device(WAN)
    lan = nm_device(LAN)
    if not wan["managed"] or not wan["uuid"]:
        raise RuntimeError("WAN must have an active NetworkManager connection.")
    settings = command(
        "nmcli", "-g", "ipv4.method,connection.autoconnect",
        "connection", "show", "uuid", wan["uuid"],
    ).stdout.splitlines()
    if settings != ["auto", "yes"]:
        raise RuntimeError("WAN must use an autoconnecting DHCP profile.")
    wan_addresses = [
        item for item in links[WAN].get("addr_info", [])
        if item.get("family") == "inet" and item.get("dynamic") is True
    ]
    if len(wan_addresses) != 1:
        raise RuntimeError("WAN must have exactly one DHCP IPv4 address.")
    if any(item.get("family") == "inet" for item in links[LAN].get("addr_info", [])):
        raise RuntimeError("The printer port already has an IPv4 address.")
    routes = json.loads(command("ip", "-j", "-4", "route", "show", "default").stdout)
    if any(route.get("metric", 0) == ROUTE_METRIC for route in routes):
        raise RuntimeError("The reserved bridge route metric is already in use.")
    listeners = command("ss", "-H", "-ltn", "sport", "=", ":8080").stdout.splitlines()
    if not any(len(line.split()) >= 4 and line.split()[3] in {"0.0.0.0:8080", "*:8080"} for line in listeners):
        raise RuntimeError("The web app must listen on all IPv4 addresses on port 8080.")
    mac = links[WAN].get("address", "")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
        raise RuntimeError("Cannot determine the WAN MAC address.")
    return {"wan": wan, "lan": lan, "mac": mac}


def lease_hook(action):
    bridge = load(STATE)["bridge"]
    if os.environ.get("interface") != bridge:
        raise RuntimeError("Unexpected DHCP interface.")
    if action == "deconfig":
        if LEASE.exists():
            previous = load(LEASE)
            command("ip", "-4", "route", "del", "default", "via", previous["gateway"],
                    "dev", bridge, "metric", str(ROUTE_METRIC))
            command("resolvectl", "revert", bridge)
        command("ip", "-4", "address", "flush", "dev", bridge)
        if LEASE.exists():
            LEASE.unlink()
        return
    if action not in {"bound", "renew"}:
        return
    address = ipaddress.IPv4Address(os.environ["ip"])
    network = ipaddress.IPv4Network("0.0.0.0/" + os.environ["subnet"])
    routers = os.environ.get("router", "").split()
    dns = os.environ.get("dns", "").split()
    if not routers or not dns:
        raise RuntimeError("DHCP did not provide a gateway and DNS server.")
    gateway = str(ipaddress.IPv4Address(routers[0]))
    servers = [str(ipaddress.IPv4Address(item)) for item in dns]
    new_address = f"{address}/{network.prefixlen}"
    current = interfaces().get(bridge)
    if current is None:
        raise RuntimeError("Bridge disappeared during DHCP configuration.")
    command("ip", "address", "replace", new_address, "dev", bridge)
    for entry in current.get("addr_info", []):
        if entry.get("family") == "inet":
            old = f"{entry['local']}/{entry['prefixlen']}"
            if old != new_address:
                command("ip", "address", "del", old, "dev", bridge)
    previous = load(LEASE) if LEASE.exists() else None
    if previous and previous["gateway"] != gateway:
        command("ip", "-4", "route", "del", "default", "via", previous["gateway"],
                "dev", bridge, "metric", str(ROUTE_METRIC))
        previous = None
    if not previous:
        command("ip", "-4", "route", "add", "default", "via", gateway,
                "dev", bridge, "metric", str(ROUTE_METRIC))
    command("resolvectl", "dns", bridge, *servers)
    domains = os.environ.get("search", "").split()
    if not domains and os.environ.get("domain"):
        domains = [os.environ["domain"]]
    for domain in domains:
        if len(domain) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in domain.rstrip(".").split(".")
        ):
            raise RuntimeError("DHCP provided an invalid DNS search domain.")
    if domains:
        command("resolvectl", "domain", bridge, *domains)
    command("resolvectl", "default-route", bridge, "yes")
    store(LEASE, {"address": str(address), "gateway": gateway, "dns": servers})


def apply_firewall(state):
    printer = validate_printer(state["printer"])["ip"]
    rules = f"""
table bridge {state["bridge_table"]} {{
    chain prerouting {{
        type filter hook prerouting priority -300; policy accept;
        iifname "{WAN}" ether type ip ip daddr {printer} tcp dport 8080 ether daddr set {state["mac"]} meta pkttype set host
    }}
}}
table ip {state["nat_table"]} {{
    chain prerouting {{
        type nat hook prerouting priority dstnat; policy accept;
        iifname "{state["bridge"]}" ip daddr {printer} tcp dport 8080 redirect to :8080
    }}
}}
"""
    command("nft", "-f", "-", input_text=rules)


def run_bridge():
    state = preflight()
    state["printer"] = validate_printer(load(ACTIVE_PRINTER_CONFIG))
    suffix = uuid.uuid4().hex[:10]
    state.update(bridge="nr" + suffix, bridge_table="nrbridge_" + suffix, nat_table="nrnat_" + suffix)
    if state["bridge"] in interfaces():
        raise RuntimeError("Generated bridge name is already in use.")
    existing_tables = nft_tables()
    for family, table in (("bridge", state["bridge_table"]), ("ip", state["nat_table"])):
        if (family, table) in existing_tables:
            raise RuntimeError("Generated nftables table is already in use.")
    store(STATE, state)
    command("nmcli", "device", "set", WAN, "managed", "no")
    command("nmcli", "device", "set", LAN, "managed", "no")
    bridge = state["bridge"]
    command("ip", "link", "add", bridge, "type", "bridge")
    command("ip", "link", "set", bridge, "address", state["mac"])
    command("ip", "link", "set", bridge, "type", "bridge", "stp_state", "0")
    for port in (WAN, LAN):
        command("ip", "link", "set", port, "master", bridge)
        command("ip", "link", "set", port, "up")
    command("ip", "link", "set", bridge, "up")
    apply_firewall(state)
    client = subprocess.Popen(
        ["busybox", "udhcpc", "-f", "-n", "-t", "6", "-T", "3",
         "-i", bridge, "-s", str(Path(__file__).resolve())],
        env={"PATH": PATH, "LC_ALL": "C"},
    )
    deadline = time.monotonic() + 30
    while not LEASE.exists():
        if HOOK_ERROR.exists():
            raise RuntimeError("DHCP setup failed: " + HOOK_ERROR.read_text(encoding="utf-8"))
        if client.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError("Bridge DHCP did not complete.")
        time.sleep(0.5)
    while True:
        if client.poll() is not None:
            raise RuntimeError("Bridge DHCP client exited.")
        if HOOK_ERROR.exists():
            raise RuntimeError("DHCP renewal failed: " + HOOK_ERROR.read_text(encoding="utf-8"))
        if not LEASE.exists():
            raise RuntimeError("The bridge DHCP lease was lost.")
        for service in ("NetworkManager", "nanopi-rfid-client", "nanopi-rfid-bridge-controller"):
            if command("systemctl", "is-active", "--quiet", service, check=False).returncode:
                raise RuntimeError(f"{service} stopped while the bridge was active.")
        time.sleep(2)


def cleanup():
    if not STATE.exists():
        return
    state = load(STATE)
    errors = []
    try:
        existing_tables = nft_tables()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        existing_tables = set()
        errors.append(f"Could not inspect nftables: {exc}")
    for family, table in (("bridge", state["bridge_table"]), ("ip", state["nat_table"])):
        if (family, table) in existing_tables:
            try:
                command("nft", "delete", "table", family, table)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                errors.append(f"Could not remove {table}: {exc}")
    try:
        links = interfaces()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        links = {}
        errors.append(f"Could not inspect interfaces: {exc}")
    bridge = state["bridge"]
    if bridge in links:
        for port in (WAN, LAN):
            if port in links and links[port].get("master") == bridge:
                try:
                    command("ip", "link", "set", port, "nomaster")
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    errors.append(f"Could not detach {port}: {exc}")
        try:
            command("ip", "link", "delete", bridge)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(f"Could not delete the bridge: {exc}")
    deadline = time.monotonic() + 45
    while command("systemctl", "is-active", "--quiet", "NetworkManager", check=False).returncode:
        if time.monotonic() >= deadline:
            errors.append("NetworkManager did not recover.")
            break
        time.sleep(1)
    else:
        for name, key in ((WAN, "wan"), (LAN, "lan")):
            try:
                command("nmcli", "device", "set", name, "managed",
                        "yes" if state[key]["managed"] else "no")
                if state[key]["managed"] and state[key]["uuid"]:
                    command("nmcli", "--wait", "0", "connection", "up", "uuid",
                            state[key]["uuid"], "ifname", name)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                errors.append(f"Could not restore NetworkManager management of {name}: {exc}")
        deadline = time.monotonic() + 35
        last_error = None
        while time.monotonic() < deadline:
            try:
                if nm_device(WAN)["uuid"] == state["wan"]["uuid"]:
                    addresses = interfaces()[WAN].get("addr_info", [])
                    if any(item.get("family") == "inet" and item.get("dynamic") is True for item in addresses):
                        break
            except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                last_error = str(exc)
            time.sleep(1)
        else:
            errors.append("WAN DHCP connection was not restored" + (f": {last_error}" if last_error else "."))
    if errors:
        raise RuntimeError("; ".join(errors))
    if LEASE.exists():
        LEASE.unlink()
    if HOOK_ERROR.exists():
        HOOK_ERROR.unlink()
    STATE.unlink()


def service_active():
    return command("systemctl", "is-active", "--quiet", SERVICE, check=False).returncode == 0


def service_state():
    state = command("systemctl", "show", "--property=ActiveState", "--value", SERVICE).stdout.strip()
    if state not in {"active", "activating", "deactivating", "inactive", "failed"}:
        raise RuntimeError(f"Unexpected bridge service state: {state}")
    return state


def handle(action, config=None):
    if action == "status":
        state = service_state()
        active = state in {"active", "activating"}
        transitioning = state in {"activating", "deactivating"}
        if TIMER.exists() and state in {"inactive", "failed"}:
            unit = load(TIMER)["unit"]
            if command("systemctl", "is-active", "--quiet", unit + ".timer", check=False).returncode:
                TIMER.unlink()
        lease = load(LEASE) if active and LEASE.exists() else None
        printer = validate_printer(load(ACTIVE_PRINTER_CONFIG)) if active else printer_config()
        return {"active": active, "pending_confirmation": TIMER.exists(),
                "transitioning": transitioning,
                "needs_recovery": STATE.exists() and state in {"inactive", "failed"},
                "wan_ip": lease["address"] if lease else None, "printer": printer}
    if action == "configure":
        if service_state() not in {"inactive", "failed"} or STATE.exists() or TIMER.exists():
            raise RuntimeError("Stop the bridge before changing the printer.")
        printer = validate_printer(config)
        store(PRINTER_CONFIG, printer)
        return {"printer": printer}
    if action == "start":
        if service_state() not in {"inactive", "failed"} or STATE.exists() or TIMER.exists():
            raise RuntimeError("Bridge is active, starting, or requires recovery.")
        preflight()
        store(ACTIVE_PRINTER_CONFIG, printer_config())
        os.chmod(ACTIVE_PRINTER_CONFIG, 0o644)
        unit = "nanopi-rfid-bridge-watchdog-" + uuid.uuid4().hex
        command("systemd-run", "--quiet", "--unit=" + unit, "--on-active=120s",
                "--timer-property=AccuracySec=1s", "/usr/bin/systemctl", "stop", SERVICE)
        try:
            store(TIMER, {"unit": unit})
            command("systemctl", "--no-block", "start", SERVICE)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            command("systemctl", "stop", unit + ".timer")
            if TIMER.exists():
                TIMER.unlink()
            raise
        return {"started": True}
    if action == "confirm":
        if not service_active() or not LEASE.exists() or not TIMER.exists():
            raise RuntimeError("No bridge is awaiting confirmation.")
        unit = load(TIMER)["unit"]
        command("systemctl", "stop", unit + ".timer")
        TIMER.unlink()
        return {"confirmed": True}
    if action == "stop":
        command("systemctl", "--no-block", "stop", SERVICE)
        return {"stopping": True}
    raise ValueError("Unknown command.")


def serve():
    RUNTIME.mkdir(mode=0o750, parents=True, exist_ok=True)
    if SOCKET.exists():
        SOCKET.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(SOCKET))
        os.chmod(SOCKET, 0o660)
        server.listen(8)
        while True:
            client, _ = server.accept()
            with client:
                client.settimeout(5)
                try:
                    raw = bytearray()
                    while True:
                        chunk = client.recv(1024)
                        if not chunk:
                            break
                        raw.extend(chunk)
                        if len(raw) > 1024:
                            raise ValueError("Bridge request is too large.")
                    request = json.loads(raw)
                    if (not isinstance(request, dict) or
                            (set(request) != {"action"} and
                             not (set(request) == {"action", "config"} and request["action"] == "configure"))):
                        raise ValueError("Invalid request.")
                    with LOCK.open("a") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        response = {"ok": True, "result": handle(request["action"], request.get("config"))}
                except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
                    print(f"Bridge controller: {exc}", file=sys.stderr, flush=True)
                    response = {"ok": False, "error": str(exc)}
                try:
                    client.sendall(json.dumps(response).encode("utf-8"))
                except OSError as exc:
                    print(f"Bridge controller response failed: {exc}", file=sys.stderr, flush=True)


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Expected serve, run, cleanup, or DHCP action.")
    action = sys.argv[1]
    if action == "serve":
        serve()
    elif action == "run":
        run_bridge()
    elif action == "cleanup":
        cleanup()
    else:
        try:
            lease_hook(action)
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
            HOOK_ERROR.write_text(str(exc), encoding="utf-8")
            raise


if __name__ == "__main__":
    main()
