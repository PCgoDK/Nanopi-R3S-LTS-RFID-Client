import hmac
import os
import re
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

import serial
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from serial.tools import list_ports

from .protocol import ProtocolError, build_frame, parse_frame, parse_hex_frame, tag_reading


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
API_TOKEN = os.environ.get("RFID_APP_TOKEN", "")
ALLOWED_PORT = re.compile(r"^(COM\d+|/dev/tty(USB|ACM)\d+|/dev/serial/by-id/[A-Za-z0-9._:+-]+)$")


class ConnectRequest(BaseModel):
    port: str = Field(min_length=1, max_length=256)
    baud: int = Field(default=115200, ge=1200, le=921600)


class CommandRequest(BaseModel):
    frame: str = Field(min_length=14, max_length=1024)
    timeout: float = Field(default=0.8, ge=0.05, le=5.0)


class InventoryRequest(BaseModel):
    count: int = Field(default=65535, ge=1, le=65535)


class ReaderService:
    def __init__(self):
        self._serial = None
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._scan_stop = threading.Event()
        self._scan_thread = None
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

    def start_inventory(self, count: int):
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
        except serial.SerialException as exc:
            self._record_event("ERROR serial inventory failed: " + str(exc))
            self._scan_stop.set()

    def stop_inventory(self):
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
        self._scan_thread = None

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
app = FastAPI(title="NanoPi UHF RFID Client")


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
        reader.stop_inventory()
    except ConnectionError as exc:
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
