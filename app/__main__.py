import os

import uvicorn

from .main import API_TOKEN, app


def main():
    host = os.environ.get("RFID_HOST", "127.0.0.1")
    port = int(os.environ.get("RFID_PORT", "8000"))
    if host not in {"127.0.0.1", "localhost", "::1"} and not API_TOKEN:
        raise SystemExit("Set RFID_APP_TOKEN before exposing the app beyond localhost.")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
