import hmac
import json
import logging
import os
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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


class WoLHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 4096:
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
        except Exception:
            pass
        self._drop()

    def _drop(self) -> None:
        """Silently close the connection without sending any response."""
        self.close_connection = True
        try:
            self.connection.close()
        except Exception:
            pass

    def send_error(self, code, message=None, explain=None) -> None:
        """Override to suppress all HTTP error responses (400, 404, 501, etc.)."""
        self._drop()

    def handle_one_request(self) -> None:
        """Override to drop non-POST requests before they generate any response."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if not self.raw_requestline or len(self.raw_requestline) > 65536:
                self.close_connection = True
                return
            if not self.parse_request():
                # parse_request would normally send a 400; send_error override silences it
                return
            if self.command == "POST":
                self.do_POST()
            else:
                self._drop()
        except Exception:
            self.close_connection = True

    def log_message(self, format, *args) -> None:
        pass  # suppress per-request access logs


if __name__ == "__main__":
    log.info("WoL server starting on port 8080 (MAC=%s, broadcast=%s)", MAC_ADDRESS, BROADCAST_IP)
    server = HTTPServer(("0.0.0.0", 8080), WoLHandler)
    server.serve_forever()