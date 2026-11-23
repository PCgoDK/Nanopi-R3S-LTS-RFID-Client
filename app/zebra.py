import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class ZebraDeliveryError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def send_tag_read(
    server_url: str,
    token: str,
    reader_id: int,
    epc: str,
    job_id: str,
    timestamp: str,
    values: dict[str, str],
    ca_file: str | None = None,
) -> int:
    url = urlsplit(server_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query
        or url.fragment
    ):
        raise ValueError("Zebra server address must be an HTTPS origin without a path.")
    if not token or any(char in token for char in "\r\n"):
        raise ValueError("A valid Zebra server token is required.")
    if not isinstance(reader_id, int) or isinstance(reader_id, bool) or reader_id < 1:
        raise ValueError("A positive Zebra reader ID is required.")
    epc = epc.strip().replace(" ", "").upper()
    if not re.fullmatch(r"(?:[0-9A-F]{2}){1,64}", epc):
        raise ValueError("A valid hexadecimal EPC is required; NoTag cannot be sent.")
    if not job_id or not timestamp:
        raise ValueError("Job ID and timestamp are required.")

    raw_payload = json.dumps(
        {"job_id": job_id, "timestamp": timestamp, "values": values},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    body = json.dumps(
        {"reader_id": reader_id, "epc_hex": epc, "raw_payload": raw_payload},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        server_url.rstrip("/") + "/api/v1/tag-reads",
        data=body,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    context = ssl.create_default_context(cafile=ca_file)
    opener = build_opener(HTTPSHandler(context=context), NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            if response.status != 201:
                raise ZebraDeliveryError("Unexpected server response; delivery status is uncertain.")
            try:
                result = json.load(response)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ZebraDeliveryError("Invalid server response; the record may have been stored.") from exc
    except HTTPError as exc:
        if exc.code >= 500 or exc.code == 408:
            raise ZebraDeliveryError(
                f"Zebra server returned HTTP {exc.code}; the record may have been stored."
            ) from exc
        raise ZebraDeliveryError(f"Zebra server rejected the request (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ZebraDeliveryError("Could not confirm delivery; the server may have received the record.") from exc

    if (
        not isinstance(result, dict)
        or not isinstance(result.get("id"), int)
        or isinstance(result["id"], bool)
        or result["id"] < 1
        or not isinstance(result.get("reader_id"), int)
        or isinstance(result["reader_id"], bool)
        or result.get("reader_id") != reader_id
        or result.get("epc_hex") != epc
    ):
        raise ZebraDeliveryError("Unexpected server response; the record may have been stored.")
    return result["id"]
