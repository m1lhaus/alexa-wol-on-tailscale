"""Tests for the Wake-on-LAN HTTP server (server/app.py).

Run with:
    export $(grep -v '^#' .env | xargs) && python -m unittest test_app -v
"""

import http.client
import json
import os
import socket
import threading
import unittest
from http.server import HTTPServer
from unittest.mock import patch

# Force env vars before importing app so module-level constants initialise correctly.
# Direct assignment (not setdefault) is required because the shell may already have
# these set to empty strings via the VS Code launch configuration.
_TOKEN = "test_token_abc123"
_MAC = "AA:BB:CC:DD:EE:FF"
_BROADCAST = "192.168.1.255"

os.environ["WOL_TOKEN"] = _TOKEN
os.environ["WOL_MAC"] = _MAC
os.environ["WOL_BROADCAST"] = _BROADCAST

import app  # noqa: E402  (import after env setup is intentional)

# Also patch module-level constants in case app was already imported with stale values.
app.TOKEN = _TOKEN.encode()
app.MAC_ADDRESS = _MAC
app.BROADCAST_IP = _BROADCAST


# ---------------------------------------------------------------------------
# Unit tests — send_magic_packet
# ---------------------------------------------------------------------------


class TestSendMagicPacket(unittest.TestCase):
    """Verify magic-packet construction without touching the real network."""

    def _call(self, mac: str, broadcast: str = "255.255.255.255"):
        """Call send_magic_packet and return the (payload, addr) passed to sendto."""
        with patch("socket.socket") as mock_socket_cls:
            mock_sock = mock_socket_cls.return_value.__enter__.return_value
            app.send_magic_packet(mac, broadcast)
        return mock_sock

    def test_payload_structure(self):
        """Magic packet = 6×0xFF + target MAC repeated 16 times."""
        mac = "AA:BB:CC:DD:EE:FF"
        mac_bytes = bytes.fromhex("AABBCCDDEEFF")
        expected = b"\xff" * 6 + mac_bytes * 16

        mock_sock = self._call(mac, "192.168.1.255")

        mock_sock.setsockopt.assert_called_once()
        payload, addr = mock_sock.sendto.call_args[0]
        self.assertEqual(payload, expected)
        self.assertEqual(addr, ("192.168.1.255", app.WOL_PORT))

    def test_colon_and_hyphen_mac_produce_same_packet(self):
        """Both 'AA:BB:…' and 'AA-BB-…' formats must yield identical packets."""
        with patch("socket.socket") as mock_cls:
            sock = mock_cls.return_value.__enter__.return_value

            app.send_magic_packet("AA:BB:CC:DD:EE:FF", "255.255.255.255")
            colon_payload = sock.sendto.call_args[0][0]
            sock.reset_mock()

            app.send_magic_packet("AA-BB-CC-DD-EE-FF", "255.255.255.255")
            hyphen_payload = sock.sendto.call_args[0][0]

        self.assertEqual(colon_payload, hyphen_payload)


# ---------------------------------------------------------------------------
# Integration tests — HTTP server
# ---------------------------------------------------------------------------


class TestWoLHTTPServer(unittest.TestCase):
    """Spin up a real HTTPServer on an ephemeral port and send live HTTP requests."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), app.WoLHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, body: bytes) -> "http.client.HTTPResponse | None":
        """POST *body*; return the response, or None when silently dropped."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request(
                "POST",
                "/wol",
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            return conn.getresponse()
        except (ConnectionResetError, http.client.RemoteDisconnected, BrokenPipeError, OSError):
            return None
        finally:
            conn.close()

    def _request(self, method: str) -> "http.client.HTTPResponse | None":
        """Send *method* with an empty body; return response or None if dropped."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            conn.request(method, "/", headers={"Content-Length": "0"})
            return conn.getresponse()
        except (ConnectionResetError, http.client.RemoteDisconnected, BrokenPipeError, OSError):
            return None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_valid_token_returns_200(self):
        """Correct token → 200 OK and exactly one magic-packet call."""
        payload = json.dumps({"token": _TOKEN}).encode()
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(payload)

        self.assertIsNotNone(resp, "connection must not be dropped for a valid request")
        self.assertEqual(resp.status, 200)
        mock_send.assert_called_once_with(_MAC, _BROADCAST)

    def test_send_magic_packet_failure_drops_connection(self):
        """If send_magic_packet raises, the server must drop rather than return 200."""
        payload = json.dumps({"token": _TOKEN}).encode()
        with patch.object(app, "send_magic_packet", side_effect=OSError("network error")):
            resp = self._post(payload)
        self.assertIsNone(resp, "socket failure must not produce a 200 response")

    def test_connection_timeout_is_set(self):
        """setup() must apply _CONNECTION_TIMEOUT to the socket."""
        from unittest.mock import MagicMock
        handler = app.WoLHandler.__new__(app.WoLHandler)
        handler.connection = MagicMock()
        handler.request = handler.connection
        handler.client_address = ("127.0.0.1", 9999)
        handler.server = self.server
        with patch("socketserver.StreamRequestHandler.setup"):
            app.WoLHandler.setup(handler)
        handler.connection.settimeout.assert_called_once_with(app._CONNECTION_TIMEOUT)

    # ------------------------------------------------------------------
    # Auth / token failures
    # ------------------------------------------------------------------

    def test_wrong_token_is_dropped(self):
        payload = json.dumps({"token": "definitely_wrong_token"}).encode()
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(payload)

        self.assertIsNone(resp, "wrong token must silently drop the connection")
        mock_send.assert_not_called()

    def test_empty_token_is_dropped(self):
        payload = json.dumps({"token": ""}).encode()
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(payload)

        self.assertIsNone(resp)
        mock_send.assert_not_called()

    def test_missing_token_field_is_dropped(self):
        """JSON body without a 'token' key must be rejected."""
        payload = json.dumps({"action": "wake"}).encode()
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(payload)

        self.assertIsNone(resp)
        mock_send.assert_not_called()

    def test_token_with_extra_whitespace_is_dropped(self):
        """Token comparison is exact — surrounding whitespace must not match."""
        payload = json.dumps({"token": f" {_TOKEN} "}).encode()
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(payload)

        self.assertIsNone(resp)
        mock_send.assert_not_called()

    # ------------------------------------------------------------------
    # Malformed / oversized body
    # ------------------------------------------------------------------

    def test_invalid_json_is_dropped(self):
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(b"this is not json")

        self.assertIsNone(resp)
        mock_send.assert_not_called()

    def test_empty_body_is_dropped(self):
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(b"")

        self.assertIsNone(resp)
        mock_send.assert_not_called()

    def test_oversized_body_is_dropped(self):
        """Content-Length > 4096 must be rejected before the body is read."""
        body = b"x" * 4097
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(body)

        self.assertIsNone(resp)
        mock_send.assert_not_called()

    def test_body_at_size_limit_is_accepted(self):
        """Content-Length == 4096 is within the allowed limit and must succeed."""
        base = json.dumps({"token": _TOKEN, "pad": ""}).encode()
        # Fill "pad" value so the total reaches exactly 4096 bytes
        pad_len = 4096 - len(base) + len('""') - 2  # replace empty "" with pad_len chars
        if pad_len < 0:
            self.skipTest("Token too long to construct a 4096-byte payload")
        payload = json.dumps({"token": _TOKEN, "pad": "x" * pad_len}).encode()
        self.assertLessEqual(len(payload), 4096, "payload construction error")

        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(payload)

        self.assertIsNotNone(resp)
        self.assertEqual(resp.status, 200)
        mock_send.assert_called_once()

    # ------------------------------------------------------------------
    # Rejected HTTP methods
    # ------------------------------------------------------------------

    def test_get_is_dropped(self):
        self.assertIsNone(self._request("GET"))

    def test_put_is_dropped(self):
        self.assertIsNone(self._request("PUT"))

    def test_delete_is_dropped(self):
        self.assertIsNone(self._request("DELETE"))

    def test_head_is_dropped(self):
        self.assertIsNone(self._request("HEAD"))

    # ------------------------------------------------------------------
    # Content-Length edge cases
    # ------------------------------------------------------------------

    def test_negative_content_length_is_dropped(self):
        """Negative Content-Length must be rejected without reading the body."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("127.0.0.1", self.port))
            s.sendall(
                b"POST /wol HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: -1\r\n"
                b"\r\n"
            )
            s.shutdown(socket.SHUT_WR)
            self.assertEqual(self._raw_recv_all(s), b"")

    def test_non_string_token_is_dropped(self):
        """Non-string token values (e.g. integers) must not match."""
        payload = json.dumps({"token": 12345}).encode()
        with patch.object(app, "send_magic_packet") as mock_send:
            resp = self._post(payload)
        self.assertIsNone(resp)
        mock_send.assert_not_called()

    # ------------------------------------------------------------------
    # Response headers
    # ------------------------------------------------------------------

    def test_successful_response_sends_connection_close(self):
        """200 OK must include Connection: close to prevent connection reuse."""
        payload = json.dumps({"token": _TOKEN}).encode()
        with patch.object(app, "send_magic_packet"):
            resp = self._post(payload)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Connection"), "close")

    # ------------------------------------------------------------------
    # Raw-socket edge cases (cover handle_one_request branches)
    # ------------------------------------------------------------------

    def _raw_recv_all(self, s: socket.socket) -> bytes:
        """Drain *s* until EOF (or timeout), return all received bytes."""
        s.settimeout(1)
        buf = b""
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        return buf

    def test_empty_requestline_closes_connection(self):
        """EOF before any data → raw_requestline == b'' → connection dropped."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("127.0.0.1", self.port))
            s.shutdown(socket.SHUT_WR)  # signal EOF immediately
            self.assertEqual(self._raw_recv_all(s), b"")

    def test_oversized_requestline_closes_connection(self):
        """Request line > 65536 bytes → len check triggers → connection dropped."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("127.0.0.1", self.port))
            # readline(65537) returns 65537 bytes when no newline is found in that window
            s.sendall(b"X" * 65537)
            s.shutdown(socket.SHUT_WR)
            self.assertEqual(self._raw_recv_all(s), b"")

    def test_malformed_requestline_drops_connection(self):
        """Bare CRLF → parse_request() gets words=[] → returns False → dropped."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("127.0.0.1", self.port))
            # empty request line; parse_request returns False
            s.sendall(b"\r\n")
            s.shutdown(socket.SHUT_WR)
            self.assertEqual(self._raw_recv_all(s), b"")


# ---------------------------------------------------------------------------
# Unit tests — _validate_config
# ---------------------------------------------------------------------------


class TestValidateConfig(unittest.TestCase):
    """Verify that startup configuration validation catches bad inputs."""

    def test_empty_token_raises(self):
        with patch.object(app, "TOKEN", b""):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_token_too_short_raises(self):
        with patch.object(app, "TOKEN", b"tooshort"):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_token_at_minimum_length_is_accepted(self):
        with patch.object(app, "TOKEN", b"a" * app._MIN_TOKEN_LEN):
            app._validate_config()  # must not raise

    def test_invalid_mac_raises(self):
        with patch.object(app, "MAC_ADDRESS", "not-a-mac"):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_valid_colon_mac_is_accepted(self):
        with patch.object(app, "MAC_ADDRESS", "AA:BB:CC:DD:EE:FF"):
            app._validate_config()

    def test_valid_hyphen_mac_is_accepted(self):
        with patch.object(app, "MAC_ADDRESS", "AA-BB-CC-DD-EE-FF"):
            app._validate_config()

    def test_valid_bare_mac_is_accepted(self):
        with patch.object(app, "MAC_ADDRESS", "AABBCCDDEEFF"):
            app._validate_config()

    def test_invalid_broadcast_raises(self):
        with patch.object(app, "BROADCAST_IP", "not.an.ip"):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_valid_config_does_not_raise(self):
        """The test-suite values themselves must pass validation."""
        app._validate_config()

    def test_zero_connection_timeout_raises(self):
        with patch.object(app, "_CONNECTION_TIMEOUT", 0.0):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_negative_connection_timeout_raises(self):
        with patch.object(app, "_CONNECTION_TIMEOUT", -1.0):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_zero_port_raises(self):
        with patch.object(app, "WOL_PORT", 0):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_port_above_65535_raises(self):
        with patch.object(app, "WOL_PORT", 70000):
            with self.assertRaises(ValueError):
                app._validate_config()

    def test_negative_port_raises(self):
        with patch.object(app, "WOL_PORT", -1):
            with self.assertRaises(ValueError):
                app._validate_config()


if __name__ == "__main__":
    unittest.main(verbosity=2)
