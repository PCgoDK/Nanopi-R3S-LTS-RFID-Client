import hmac
import ipaddress
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit

import serial
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt
from serial.tools import list_ports

from .bridge import bridge_request, capture_activity, capture_status
from .protocol import ProtocolError, build_frame, parse_frame, parse_hex_frame, tag_reading
from .joblog import JobLog
from .rfid_logging import RFIDJobLogger
from .sniffer import ExtractionRule, RuleConflictError, RuleStore, extract_values


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ALLOW_UNAUTHENTICATED = os.environ.get("RFID_ALLOW_UNAUTHENTICATED") == "1"
API_TOKEN = "" if ALLOW_UNAUTHENTICATED else os.environ.get("RFID_APP_TOKEN", "")
ALLOWED_PORT = re.compile(r"^(COM\d+|/dev/tty(USB|ACM)\d+|/dev/serial/by-id/[A-Za-z0-9._:+-]+)$")
logger = logging.getLogger(__name__)


class ConnectRequest(BaseModel):
    port: str = Field(min_length=1, max_length=256)
    baud: int = Field(default=115200, ge=1200, le=921600)


class CommandRequest(BaseModel):
    frame: str = Field(min_length=14, max_length=1024)
    timeout: float = Field(default=0.8, ge=0.05, le=5.0)


class InventoryRequest(BaseModel):
    count: int = Field(default=65535, ge=1, le=65535)


class SnifferRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    marker: str = Field(min_length=1, max_length=256)
    length: StrictInt | None = Field(default=None, ge=1, le=100000)
    terminator: str | None = Field(default=None, max_length=256)
    version: StrictInt | None = Field(default=None, ge=1)

    def extraction_rule(self):
        return ExtractionRule(self.name.strip(), self.marker, self.length, self.terminator)


class SnifferPreviewRequest(BaseModel):
    text: str = Field(max_length=100000)


class SnifferLinkRequest(BaseModel):
    observation_id: StrictInt = Field(ge=1)


class PrinterConfigRequest(BaseModel):
    ip: str = Field(min_length=1, max_length=15)
    port: StrictInt = Field(ge=1, le=65535)


def serialized_reader(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._operation_lock:
            return method(self, *args, **kwargs)
    return wrapped


class ReaderService:
    def __init__(self):
        self._serial = None
        self._io_lock = threading.Lock()
        self._operation_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._scan_stop = threading.Event()
        self._scan_thread = None
        self._inventory_owner = None
        self._inventory_error = None
        self._inventory_response_received = False
        self.observation_callback = None
        self._cw_timer = None
        self._receive_buffer = bytearray()
        self._tags = OrderedDict()
        self._events = deque(maxlen=500)
        self._tx_bytes = 0
        self._rx_bytes = 0
        self._rx_frames = 0
        self._port = None
        self._baud = None

    @property
    def connected(self):
        return self._serial is not None and self._serial.is_open

    def ports(self):
        return [{"device": item.device, "description": item.description} for item in list_ports.comports()]

    @serialized_reader
    def connect(self, port: str, baud: int):
        if not ALLOWED_PORT.fullmatch(port):
            raise ValueError("Choose a listed serial port or a supported Linux UART device path.")
        if self.connected:
            self.disconnect()
        try:
            connection = serial.Serial(port=port, baudrate=baud, timeout=0.05, write_timeout=1)
        except serial.SerialException as exc:
            raise ConnectionError(str(exc)) from exc

        with self._state_lock:
            self._serial = connection
            self._port = port
            self._baud = baud
            self._scan_thread = None
            self._scan_stop.clear()
            self._receive_buffer.clear()
            self._tags.clear()
            self._events.clear()
            self._tx_bytes = 0
            self._rx_bytes = 0
            self._rx_frames = 0

    @serialized_reader
    def disconnect(self):
        self.stop_inventory()
        if self._cw_timer is not None:
            self._turn_cw_off()
        with self._state_lock:
            connection = self._serial
            self._serial = None
            self._port = None
            self._baud = None
        if connection is not None and connection.is_open:
            connection.close()

    def _require_connection(self):
        connection = self._serial
        if connection is None or not connection.is_open:
            raise ConnectionError("Connect to a serial port first.")
        return connection

    def _read_frame(self, connection, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            buffer = self._receive_buffer
            if len(buffer) >= 5:
                frame_length = int.from_bytes(buffer[3:5], "big") + 7
                if len(buffer) >= frame_length:
                    raw = bytes(buffer[:frame_length])
                    del buffer[:frame_length]
                    try:
                        parse_frame(raw)
                    except ProtocolError as exc:
                        self._record_event("RX invalid frame: " + str(exc) + " " + raw.hex(" ").upper())
                        continue
                    return raw
            byte = connection.read(1)
            if not byte:
                continue
            if not buffer:
                if byte[0] != 0xBB:
                    continue
            buffer.extend(byte)
        return None

    def _record_frame(self, raw: bytes):
        frame = parse_frame(raw)
        reading = tag_reading(frame)
        with self._state_lock:
            self._rx_bytes += len(raw)
            self._rx_frames += 1
            self._events.append("RX " + raw.hex(" ").upper())
            if reading:
                current = self._tags.get(reading["epc"])
                if current is None:
                    reading["count"] = 1
                    self._tags[reading["epc"]] = reading
                else:
                    current["count"] += 1
                    current["pc"] = reading["pc"]
                    current["crc"] = reading["crc"]
                    current["rssi_dbm"] = reading["rssi_dbm"]
                    self._tags.move_to_end(reading["epc"])
                while len(self._tags) > 200:
                    self._tags.popitem(last=False)

    def _record_event(self, message: str):
        with self._state_lock:
            self._events.append(message)

    @serialized_reader
    def send(self, raw: bytes, timeout: float):
        frame = parse_frame(raw)
        if frame.frame_type != 0:
            raise ValueError("Only command frames (type 0x00) can be sent.")
        connection = self._require_connection()
        with self._io_lock:
            if self.inventory_running:
                raise RuntimeError("Stop continuous inventory before sending another command.")
            connection.write(raw)
            with self._state_lock:
                self._tx_bytes += len(raw)
                self._events.append("TX " + raw.hex(" ").upper())
            if frame.command == 0xB0:
                if frame.payload == b"\xFF":
                    self._schedule_cw_off()
                else:
                    self._cancel_cw_timer()

            responses = []
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                received = self._read_frame(connection, deadline - time.monotonic())
                if received is None:
                    if responses:
                        break
                    continue
                self._record_frame(received)
                responses.append(received.hex(" ").upper())
            return responses

    def _cancel_cw_timer(self):
        timer = self._cw_timer
        self._cw_timer = None
        if timer is not None:
            timer.cancel()

    def _schedule_cw_off(self):
        self._cancel_cw_timer()
        timer = threading.Timer(2.0, self._turn_cw_off)
        timer.daemon = True
        self._cw_timer = timer
        timer.start()

    def _turn_cw_off(self):
        timer = self._cw_timer
        if timer is None:
            return
        self._cw_timer = None
        timer.cancel()
        connection = self._serial
        if connection is None or not connection.is_open:
            return
        frame = build_frame(0xB0, b"\x00")
        try:
            with self._io_lock:
                connection.write(frame)
            with self._state_lock:
                self._tx_bytes += len(frame)
                self._events.append("TX " + frame.hex(" ").upper() + " (automatic CW off)")
        except serial.SerialException as exc:
            self._record_event("ERROR automatic CW off failed: " + str(exc))

    @property
    def inventory_running(self):
        thread = self._scan_thread
        return thread is not None and thread.is_alive()

    @serialized_reader
    def start_inventory(self, count: int, owner="manual"):
        connection = self._require_connection()
        if self.inventory_running:
            raise RuntimeError("Continuous inventory is already running.")
        if self._cw_timer is not None:
            self._turn_cw_off()
        frame = build_frame(0x27, bytes((0x22, (count >> 8) & 0xFF, count & 0xFF)))
        with self._io_lock:
            connection.write(frame)
        with self._state_lock:
            self._tx_bytes += len(frame)
            self._events.append("TX " + frame.hex(" ").upper())
        self._inventory_owner = owner
        self._inventory_error = None
        self._inventory_response_received = False
        self._scan_stop.clear()
        self._scan_thread = threading.Thread(target=self._inventory_loop, daemon=True)
        self._scan_thread.start()

    def _inventory_loop(self):
        connection = self._serial
        try:
            while connection is not None and connection.is_open and not self._scan_stop.is_set():
                received = self._read_frame(connection, 0.2)
                if received is not None:
                    self._record_frame(received)
                    frame = parse_frame(received)
                    if self._inventory_owner == "sniffer":
                        if frame.frame_type == 0x01 and frame.command == 0xFF and len(frame.payload) == 1:
                            if frame.payload[0] == 0x15:
                                self._inventory_response_received = True
                            else:
                                self._inventory_error = f"RFID-læseren meldte fejl 0x{frame.payload[0]:02X}."
                                self._record_event("ERROR " + self._inventory_error)
                                break
                        elif frame.frame_type == 0x02:
                            reading = tag_reading(frame)
                            if reading and reading["epc"] and int.from_bytes(frame.payload[1:3], "big") >> 11:
                                self._inventory_response_received = True
                                if self.observation_callback:
                                    self.observation_callback(reading["epc"], time.time())
        except serial.SerialException as exc:
            self._inventory_error = str(exc)
            self._record_event("ERROR serial inventory failed: " + str(exc))
            self._scan_stop.set()

    @serialized_reader
    def stop_inventory(self, owner=None):
        if owner == "manual" and self._inventory_owner == "sniffer":
            raise RuntimeError("Datasnifferen bruger RFID-læseren, mens bridgen er aktiv.")
        if owner == "sniffer" and self._inventory_owner != "sniffer":
            return
        thread = self._scan_thread
        if thread is None:
            return
        self._scan_stop.set()
        connection = self._serial
        if connection is not None and connection.is_open:
            frame = build_frame(0x28)
            with self._io_lock:
                connection.write(frame)
            with self._state_lock:
                self._tx_bytes += len(frame)
                self._events.append("TX " + frame.hex(" ").upper())
        thread.join(timeout=1)
        if thread.is_alive():
            raise RuntimeError("RFID-inventeringen kunne ikke stoppes.")
        self._scan_thread = None
        self._inventory_owner = None
        self._inventory_error = None

    def state(self):
        with self._state_lock:
            return {
                "connected": self.connected,
                "port": self._port,
                "baud": self._baud,
                "inventory_running": self.inventory_running,
                "tx_bytes": self._tx_bytes,
                "rx_bytes": self._rx_bytes,
                "rx_frames": self._rx_frames,
                "tags": list(self._tags.values()),
                "events": list(self._events),
            }


reader = ReaderService()
state_dir = Path(os.environ.get("RFID_STATE_DIR", Path.home() / ".local" / "state" / "nanopi-rfid-client"))
sniffer_rules = RuleStore(state_dir / "sniffer-rules.sqlite3")
job_log = JobLog(state_dir / "jobs.sqlite3")
rfid_job_logger = RFIDJobLogger(reader, bridge_request, capture_activity, job_log, logger)
reader.observation_callback = rfid_job_logger.observe


@asynccontextmanager
async def lifespan(_app):
    rfid_job_logger.start()
    try:
        yield
    finally:
        rfid_job_logger.stop()


app = FastAPI(title="NanoPi UHF RFID Client", lifespan=lifespan)


@app.middleware("http")
async def protect_api(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in {"/api/config", "/api/health"} and API_TOKEN:
        supplied = request.headers.get("x-api-token", "")
        if not hmac.compare_digest(supplied, API_TOKEN):
            return JSONResponse(status_code=401, content={"detail": "Enter the app access token."})
    return await call_next(request)


@app.get("/api/config")
def get_app_config():
    return {"auth_required": bool(API_TOKEN)}


@app.get("/api/health")
def health():
    return {"ok": True}


def bridge_action(action):
    try:
        return bridge_request(action)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Bridge controller error: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/bridge")
def get_bridge_status():
    return bridge_action("status")


@app.get("/api/sniffer/capture")
def get_capture_status():
    try:
        return capture_status()
    except (OSError, ValueError) as exc:
        logger.warning("Printer capture unavailable: %s", exc)
        raise HTTPException(status_code=503, detail="Printeropsamling er ikke tilgængelig.") from exc


@app.get("/api/sniffer/log")
def get_job_log():
    try:
        return {"jobs": job_log.list_jobs(), "observations": job_log.list_observations(),
                "rfid": rfid_job_logger.snapshot()}
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.error("Datasniffer job log failed: %s", exc)
        raise HTTPException(status_code=503, detail="Kunne ikke læse jobloggen.") from exc


@app.get("/api/sniffer/log.csv")
def export_job_log():
    try:
        job_log.list_jobs(limit=1)
    except (OSError, sqlite3.Error, ValueError) as exc:
        logger.error("Datasniffer CSV export failed: %s", exc)
        raise HTTPException(status_code=503, detail="Kunne ikke eksportere jobloggen.") from exc
    def rows():
        try:
            yield from job_log.csv_rows()
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.error("Datasniffer CSV stream failed: %s", exc)
            raise
    return StreamingResponse(rows(), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="datasniffer-log.csv"'})


def require_log_origin(request: Request):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="Logændringer kræver application/json.")
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Ugyldig afsender.") from exc
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="Logændringer kræver samme afsender.")


@app.post("/api/sniffer/log/{job_id}/epcs")
def link_job_epc(job_id: str, payload: SnifferLinkRequest, request: Request):
    require_log_origin(request)
    try:
        linked = job_log.link(job_id, payload.observation_id)
    except (OSError, sqlite3.Error) as exc:
        logger.error("Could not link RFID observation: %s", exc)
        raise HTTPException(status_code=503, detail="Kunne ikke gemme koblingen.") from exc
    if not linked:
        raise HTTPException(status_code=404, detail="Job eller RFID-læsning findes ikke længere.")
    return {"linked": True}


@app.delete("/api/sniffer/log/{job_id}/epcs/{observation_id}")
def unlink_job_epc(job_id: str, observation_id: int, request: Request):
    require_log_origin(request)
    try:
        deleted = job_log.unlink(job_id, observation_id)
    except (OSError, sqlite3.Error) as exc:
        logger.error("Could not unlink RFID observation: %s", exc)
        raise HTTPException(status_code=503, detail="Kunne ikke fjerne koblingen.") from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Koblingen findes ikke længere.")
    return {"deleted": True}


@app.post("/api/sniffer/log/{job_id}/no-tag")
def mark_job_no_tag(job_id: str, request: Request):
    require_log_origin(request)
    try:
        changed = job_log.mark_no_tag(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        logger.error("Could not mark job NoTag: %s", exc)
        raise HTTPException(status_code=503, detail="Kunne ikke gemme NoTag.") from exc
    if not changed:
        raise HTTPException(status_code=404, detail="Jobbet findes ikke længere.")
    return {"updated": True}


@app.delete("/api/sniffer/log/{job_id}/no-tag")
def clear_job_no_tag(job_id: str, request: Request):
    require_log_origin(request)
    try:
        changed = job_log.clear_no_tag(job_id)
    except (OSError, sqlite3.Error) as exc:
        logger.error("Could not clear NoTag: %s", exc)
        raise HTTPException(status_code=503, detail="Kunne ikke ændre NoTag.") from exc
    if not changed:
        raise HTTPException(status_code=404, detail="Jobbet er ikke markeret NoTag.")
    return {"updated": True}


@app.post("/api/bridge/config")
def configure_printer(payload: PrinterConfigRequest, request: Request):
    require_log_origin(request)
    try:
        return bridge_request("configure", {"ip": payload.ip, "port": payload.port})
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Printer configuration failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/bridge/{action}")
def change_bridge(action: str, request: Request):
    if action not in {"start", "stop", "confirm"}:
        raise HTTPException(status_code=404, detail="Unknown bridge action.")
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="Bridge actions require application/json.")
    origin = request.headers.get("origin")
    if origin:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Invalid request origin.") from exc
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
            raise HTTPException(status_code=403, detail="Bridge actions require a same-origin request.")
    if action == "confirm":
        printer = bridge_action("status")["printer"]
        if request.headers.get("host", "").lower() != f'{printer["ip"]}:8080':
            raise HTTPException(status_code=403, detail="Confirm bridge access from the printer address.")
    return bridge_action(action)


@app.get("/api/network")
def get_network():
    try:
        result = subprocess.run(
            ["ip", "-j", "-4", "address", "show", "dev", "eth0"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            env={"PATH": os.defpath, "LC_ALL": "C"},
        )
        interfaces = json.loads(result.stdout)
        if (
            not isinstance(interfaces, list) or len(interfaces) != 1
            or not isinstance(interfaces[0], dict) or interfaces[0].get("ifname") != "eth0"
            or not isinstance(interfaces[0].get("addr_info"), list)
        ):
            raise ValueError("Unexpected WAN interface data.")
        addresses = []
        for address in interfaces[0]["addr_info"]:
            if not isinstance(address, dict):
                raise ValueError("Invalid WAN address data.")
            if address.get("family") == "inet" and address.get("dynamic") is True:
                if not isinstance(address.get("local"), str):
                    raise ValueError("Invalid dynamic WAN address.")
                addresses.append(str(ipaddress.IPv4Address(address["local"])))
        if len(addresses) > 1:
            raise ValueError("Multiple dynamic WAN addresses reported.")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        logger.warning("Unable to read WAN IP information: %s", exc)
        raise HTTPException(status_code=503, detail="Unable to read WAN IP information.") from exc
    return {"wan_ip": addresses[0] if addresses else None}


def sniffer_store_error(exc: Exception):
    logger.error("Datasniffer rule storage failed: %s", exc)
    raise HTTPException(status_code=503, detail="Kunne ikke tilgå de gemte udtræksregler.") from exc


@app.get("/api/sniffer/rules")
def get_sniffer_rules():
    try:
        return {"rules": sniffer_rules.list()}
    except (OSError, sqlite3.Error) as exc:
        sniffer_store_error(exc)


def save_sniffer_rule(request: SnifferRuleRequest, rule_id: int | None = None):
    try:
        if (rule_id is None) != (request.version is None):
            raise ValueError("Versionsnummer kræves kun ved redigering.")
        rule = request.extraction_rule()
        saved_id = sniffer_rules.save(rule, rule_id, request.version)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Der findes allerede en regel med det navn.") from exc
    except RuleConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        sniffer_store_error(exc)
    if saved_id is None:
        raise HTTPException(status_code=404, detail="Reglen findes ikke længere.")
    return {"id": saved_id, "version": 1 if request.version is None else request.version + 1}


@app.post("/api/sniffer/rules", status_code=201)
def create_sniffer_rule(request: SnifferRuleRequest):
    return save_sniffer_rule(request)


@app.put("/api/sniffer/rules/{rule_id}")
def update_sniffer_rule(rule_id: int, request: SnifferRuleRequest):
    return save_sniffer_rule(request, rule_id)


@app.delete("/api/sniffer/rules/{rule_id}")
def delete_sniffer_rule(rule_id: int, version: int):
    try:
        deleted = sniffer_rules.delete(rule_id, version)
    except RuleConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, sqlite3.Error) as exc:
        sniffer_store_error(exc)
    if not deleted:
        raise HTTPException(status_code=404, detail="Reglen findes ikke længere.")
    return {"deleted": True}


@app.post("/api/sniffer/preview")
def preview_sniffer_rules(request: SnifferPreviewRequest):
    try:
        rules = [ExtractionRule(row["name"], row["marker"], row["length"], row["terminator"])
                 for row in sniffer_rules.list()]
    except (OSError, sqlite3.Error) as exc:
        sniffer_store_error(exc)
    try:
        data = request.text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise HTTPException(status_code=422, detail="Testdata er ikke gyldig Unicode.") from exc
    return {"results": extract_values(data, rules)}


@app.get("/api/ports")
def get_ports():
    return {"ports": reader.ports()}


@app.get("/api/state")
def get_state():
    return reader.state()


@app.post("/api/connect")
def connect(request: ConnectRequest):
    try:
        reader.connect(request.port, request.baud)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return reader.state()


@app.post("/api/disconnect")
def disconnect():
    reader.disconnect()
    return reader.state()


@app.post("/api/command")
def send_command(request: CommandRequest):
    try:
        frame = parse_hex_frame(request.frame)
        responses = reader.send(frame.raw, request.timeout)
    except ProtocolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except serial.SerialException as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"responses": responses, "state": reader.state()}


@app.post("/api/inventory/single")
def inventory_single():
    frame = build_frame(0x22)
    try:
        responses = reader.send(frame, 0.8)
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except serial.SerialException as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"responses": responses, "state": reader.state()}


@app.post("/api/inventory/start")
def inventory_start(request: InventoryRequest):
    try:
        reader.start_inventory(request.count)
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except serial.SerialException as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return reader.state()


@app.post("/api/inventory/stop")
def inventory_stop():
    try:
        reader.stop_inventory(owner="manual")
    except ConnectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except serial.SerialException as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return reader.state()


@app.delete("/api/tags")
def clear_tags():
    with reader._state_lock:
        reader._tags.clear()
        reader._events.append("Tag table cleared.")
    return reader.state()


@app.delete("/api/events")
def clear_events():
    with reader._state_lock:
        reader._events.clear()
    return reader.state()


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
