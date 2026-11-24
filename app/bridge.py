import json
import socket


SOCKET_PATH = "/run/nanopi-rfid-bridge/control.sock"
CAPTURE_SOCKET_PATH = "/run/nanopi-rfid-capture/capture.sock"


def bridge_request(action, config=None):
    if not hasattr(socket, "AF_UNIX"):
        raise OSError("Bridge control requires Unix domain sockets.")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(SOCKET_PATH)
        request = {"action": action}
        if config is not None:
            request["config"] = config
        client.sendall(json.dumps(request).encode("utf-8"))
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while True:
            data = client.recv(4096)
            if not data:
                break
            response.extend(data)
            if len(response) > 16384:
                raise ValueError("Bridge controller response is too large.")
    parsed = json.loads(response)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("ok"), bool):
        raise ValueError("Invalid bridge controller response.")
    if not parsed["ok"]:
        raise RuntimeError(str(parsed.get("error", "Bridge controller failed.")))
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise ValueError("Invalid bridge controller result.")
    return result


def _capture_response(activity_only):
    if not hasattr(socket, "AF_UNIX"):
        raise OSError("Printer capture requires Unix domain sockets.")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(CAPTURE_SOCKET_PATH)
        if activity_only:
            client.sendall(b"A")
        response = bytearray()
        while True:
            data = client.recv(4096)
            if not data:
                break
            response.extend(data)
            if len(response) > 700000:
                raise ValueError("Capture response is too large.")
    result = json.loads(response)
    expected = {"error", "started_job"} if activity_only else {"error", "latest", "packet_loss", "started_job"}
    if not isinstance(result, dict) or set(result) != expected:
        raise ValueError("Invalid capture response.")
    return result


def capture_status():
    return _capture_response(False)


def capture_activity():
    return _capture_response(True)
