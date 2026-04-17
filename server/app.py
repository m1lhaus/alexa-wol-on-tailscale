import itertools
import json
import logging
import os
import re
import socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

_log_level = logging.DEBUG if os.environ["WOL_DEBUG"] == "1" else logging.INFO
logging.basicConfig(level=_log_level, format="%(asctime)s %(levelname)s %(message)s")
logging.Formatter.converter = time.localtime
log = logging.getLogger(__name__)

# Secret token for authenticating incoming requests
TOKEN: bytes = os.environ["WOL_TOKEN"].encode()

# Target machine's MAC address
MAC_ADDRESS: str = os.environ["WOL_MAC"]

# Broadcast address on your LAN (usually <subnet>.255)
BROADCAST_IP: str = os.environ["WOL_BROADCAST"]

# UDP port to send WoL magic packets to (default: 9)
WOL_PORT: int = int(os.environ["WOL_PORT"])

# Minimum token length — reject trivially weak secrets at startup
_MIN_TOKEN_LEN: int = 16

# Per-connection inactivity timeout in seconds — limits slowloris / slow-read attacks
_CONNECTION_TIMEOUT: float = float(os.environ["WOL_CONN_TIMEOUT"])

_MAC_RE = re.compile(
    r"^([0-9A-Fa-f]{2}[:\-]){5}[0-9A-Fa-f]{2}$"   # colon- or hyphen-delimited
    r"|^[0-9A-Fa-f]{12}$"                         # bare 12 hex digits
)


def _validate_config() -> None:
    """Validate module-level configuration; raises ValueError on misconfiguration."""
    if len(TOKEN) < _MIN_TOKEN_LEN:
        raise ValueError(f"WOL_TOKEN must be at least {_MIN_TOKEN_LEN} characters (got {len(TOKEN)})")
    if not _MAC_RE.match(MAC_ADDRESS):
        raise ValueError(f"WOL_MAC is not a valid MAC address: {MAC_ADDRESS!r}")
    try:
        socket.inet_aton(BROADCAST_IP)
    except OSError:
        raise ValueError(f"WOL_BROADCAST is not a valid IPv4 address: {BROADCAST_IP!r}")
    if _CONNECTION_TIMEOUT <= 0:
        raise ValueError(f"WOL_CONN_TIMEOUT must be positive (got {_CONNECTION_TIMEOUT})")


def send_magic_packet(mac: str, broadcast: str) -> None:
    """Send a WoL magic packet to the specified MAC address via the specified broadcast address."""
    mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    magic = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(magic, (broadcast, WOL_PORT))


def get_local_ip() -> str:
    """Get the local IP address of the machine."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as _s:
        _s.connect(("8.8.8.8", 80))
        local_ip = _s.getsockname()[0]
    return local_ip


class WoLHandler(BaseHTTPRequestHandler):
    _req_counter = itertools.count(1)   # acts like static variable

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(_CONNECTION_TIMEOUT)
        self._req_id = next(WoLHandler._req_counter)

    def do_POST(self) -> None:

        log.debug("POST request [#%d]", self._req_id)   # due to Tailscale funnel, all client addresses are the same
        try:
            length = int(self.headers.get("Content-Length", 0))
            log.debug("Content-Length: %d", length)
            if length < 0:
                log.debug("Dropping request [#%d]: negative Content-Length", self._req_id)
                self._drop()
                return
            if length > 4096:
                log.debug("Dropping request [#%d]: Content-Length %d exceeds limit", self._req_id, length)
                self._drop()
                return
            body = self.rfile.read(length)
            data = json.loads(body)  # exception handled below
            token_raw = data.get("token")
            if not isinstance(token_raw, str):
                log.debug("Dropping request [#%d]: token field missing or not a string", self._req_id)
                self._drop()
                return
            token = token_raw.encode()
            if token == TOKEN:
                send_magic_packet(MAC_ADDRESS, BROADCAST_IP)
                log.info("WoL magic packet sent to %s via %s", MAC_ADDRESS, BROADCAST_IP)
                self.send_response(200)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
            log.debug("Dropping request [#%d]: token mismatch", self._req_id)
        except json.JSONDecodeError as e:
            log.debug("Dropping request [#%d]: JSON parse error: %s", self._req_id, e)
        except Exception as e:
            log.debug("Dropping request [#%d]: unexpected error: %s", self._req_id, e)
        self._drop()

    def _drop(self) -> None:
        """Silently close the connection without sending any response."""
        log.debug("Closing connection [#%d] without response", self._req_id)
        self.close_connection = True
        try:
            self.connection.close()
        except Exception:
            pass

    def send_error(self, code, message=None, explain=None) -> None:
        """Override to suppress all HTTP error responses (400, 404, 501, etc.)."""
        log.debug("Suppressing HTTP error %d (%s) [#%d]", code, message, self._req_id)
        self._drop()

    def handle_one_request(self) -> None:
        """Override to drop non-POST requests before they generate any response."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if not self.raw_requestline:
                log.debug("Empty request line [#%d], closing connection", self._req_id)
                self.close_connection = True
                return
            if len(self.raw_requestline) > 65536:
                log.debug("Request line too long [#%d] (%d bytes), closing connection",
                          self._req_id, len(self.raw_requestline))
                self.close_connection = True
                return
            if not self.parse_request():
                # parse_request would normally send a 400; send_error override silences it
                return
            log.debug("Received %s %s [#%d]", self.command, self.path, self._req_id)
            if self.command == "POST":
                self.do_POST()
            else:
                log.debug("Dropping non-POST request [#%d]: %s", self._req_id, self.command)
                self._drop()
        except Exception as e:
            log.debug("Exception handling request [#%d]: %s", self._req_id, e)
            self.close_connection = True

    def log_message(self, format, *args) -> None:
        pass  # suppress per-request access logs


if __name__ == "__main__":
    _validate_config()
    log.info(f"WoL server running on {get_local_ip()} starting listening on 0.0.0.0:8080 "
             f"(WoL MAC={MAC_ADDRESS}, broadcast={BROADCAST_IP})")
    log.debug("Debug mode is enabled; all incoming requests will be logged")
    server = HTTPServer(("0.0.0.0", 8080), WoLHandler)
    server.serve_forever()
