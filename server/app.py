import hmac
import json
import logging
import os
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

_log_level = logging.DEBUG if os.environ.get("WOL_DEBUG") == "1" else logging.INFO
logging.basicConfig(level=_log_level, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TOKEN: bytes = os.environ["WOL_TOKEN"].encode()
MAC_ADDRESS: str = os.environ["WOL_MAC"]
BROADCAST_IP: str = os.environ.get("WOL_BROADCAST", "255.255.255.255")
WOL_PORT: int = 9


def send_magic_packet(mac: str, broadcast: str) -> None:
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
    def do_POST(self) -> None:
        log.debug("POST request from %s", self.client_address)
        try:
            length = int(self.headers.get("Content-Length", 0))
            log.debug("Content-Length: %d", length)
            if length > 4096:
                log.debug("Dropping request: Content-Length %d exceeds limit", length)
                self._drop()
                return
            body = self.rfile.read(length)
            data = json.loads(body)
            token = data.get("token", "").encode()
            if hmac.compare_digest(token, TOKEN):
                send_magic_packet(MAC_ADDRESS, BROADCAST_IP)
                log.info("WoL magic packet sent to %s via %s", MAC_ADDRESS, BROADCAST_IP)
                self.send_response(200)
                self.end_headers()
                return
            log.debug("Dropping request: token mismatch from %s", self.client_address)
        except json.JSONDecodeError as e:
            log.debug("Dropping request: JSON parse error from %s: %s", self.client_address, e)
        except Exception as e:
            log.debug("Dropping request: unexpected error from %s: %s", self.client_address, e)
        self._drop()

    def _drop(self) -> None:
        """Silently close the connection without sending any response."""
        log.debug("Closing connection from %s without response", self.client_address)
        self.close_connection = True
        try:
            self.connection.close()
        except Exception:
            pass

    def send_error(self, code, message=None, explain=None) -> None:
        """Override to suppress all HTTP error responses (400, 404, 501, etc.)."""
        log.debug("Suppressing HTTP error %d (%s) for %s", code, message, self.client_address)
        self._drop()

    def handle_one_request(self) -> None:
        """Override to drop non-POST requests before they generate any response."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if not self.raw_requestline:
                log.debug("Empty request line from %s, closing connection", self.client_address)
                self.close_connection = True
                return
            if len(self.raw_requestline) > 65536:
                log.debug("Request line too long from %s (%d bytes), closing connection",
                          self.client_address, len(self.raw_requestline))
                self.close_connection = True
                return
            if not self.parse_request():
                # parse_request would normally send a 400; send_error override silences it
                return
            log.debug("Received %s %s from %s", self.command, self.path, self.client_address)
            if self.command == "POST":
                self.do_POST()
            else:
                log.debug("Dropping non-POST request (%s) from %s", self.command, self.client_address)
                self._drop()
        except Exception as e:
            log.debug("Exception handling request from %s: %s", self.client_address, e)
            self.close_connection = True

    def log_message(self, format, *args) -> None:
        pass  # suppress per-request access logs


if __name__ == "__main__":
    log.info(f"WoL server running on {get_local_ip()} starting listening on 0.0.0.0:8080 "
             f"(WoL MAC={MAC_ADDRESS}, broadcast={BROADCAST_IP})")
    log.debug("Debug mode is enabled; all incoming requests will be logged")
    server = HTTPServer(("0.0.0.0", 8080), WoLHandler)
    server.serve_forever()
