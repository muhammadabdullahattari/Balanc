"""Standard lightweight RFC 5321 SMTP test server with query endpoint.

Provides in-process or standalone SMTP capture for integration and browser tests,
eliminating third-party external container dependencies (e.g. Mailpit).
"""

import contextlib
import email
import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

logger = logging.getLogger("ledgerpilot.testing.smtp")

_LOCK = threading.Lock()
_MESSAGES: list[dict[str, object]] = []


def clear_messages() -> None:
    with _LOCK:
        _MESSAGES.clear()


def get_messages(
    to: str | None = None, subject_contains: str | None = None
) -> list[dict[str, object]]:
    with _LOCK:
        items = list(_MESSAGES)
    if to:
        t = to.strip().casefold()
        items = [m for m in items if t in str(m.get("To", "")).casefold()]
    if subject_contains:
        s = subject_contains.strip().casefold()
        items = [m for m in items if s in str(m.get("Subject", "")).casefold()]
    return items


class _SMTPHandler:
    def __init__(self, conn: socket.socket) -> None:
        self.conn = conn
        self.rfile = conn.makefile("rb")
        self.wfile = conn.makefile("wb")

    def send_line(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode("utf-8"))
        self.wfile.flush()

    def handle(self) -> None:
        self.send_line("220 127.0.0.1 Standard SMTP Test Server Ready")
        data_mode = False
        data_lines: list[bytes] = []
        mail_from = ""
        rcpt_to: list[str] = []

        while True:
            line = self.rfile.readline()
            if not line:
                break
            if data_mode:
                if line in (b".\r\n", b".\n", b"."):
                    raw = b"".join(data_lines)
                    try:
                        parsed = email.message_from_bytes(raw)
                        body = ""
                        if parsed.is_multipart():
                            for part in parsed.walk():
                                if part.get_content_type() == "text/plain":
                                    part_payload = part.get_payload(decode=True)
                                    if isinstance(part_payload, bytes):
                                        body = part_payload.decode("utf-8", errors="replace")
                                    elif part_payload:
                                        body = str(part_payload)
                                    break
                        else:
                            payload = parsed.get_payload(decode=True)
                            if isinstance(payload, bytes):
                                body = payload.decode("utf-8", errors="replace")
                            elif payload:
                                body = str(payload)
                            else:
                                body = str(parsed.get_payload())
                        msg_id = str(uuid4())
                        record: dict[str, object] = {
                            "ID": msg_id,
                            "From": mail_from or parsed.get("From", ""),
                            "To": ", ".join(rcpt_to) if rcpt_to else parsed.get("To", ""),
                            "Subject": parsed.get("Subject", ""),
                            "Text": body,
                            "Raw": raw.decode("utf-8", errors="replace"),
                        }
                        with _LOCK:
                            _MESSAGES.insert(0, record)
                    except Exception as err:
                        logger.warning("Failed to parse SMTP message: %s", err)
                    self.send_line("250 2.0.0 Ok: queued")
                    data_mode = False
                    data_lines.clear()
                    mail_from = ""
                    rcpt_to.clear()
                else:
                    data_lines.append(line)
                continue

            text_line = line.decode("utf-8", errors="replace").strip()
            upper = text_line.upper()

            if upper.startswith(("EHLO", "HELO")):
                self.send_line("250-127.0.0.1 Hello")
                self.send_line("250-SIZE 35882577")
                self.send_line("250-8BITMIME")
                self.send_line("250 HELP")
            elif upper.startswith("MAIL FROM:"):
                mail_from = text_line[10:].strip().strip("<>")
                self.send_line("250 2.1.0 Ok")
            elif upper.startswith("RCPT TO:"):
                rcpt_to.append(text_line[8:].strip().strip("<>"))
                self.send_line("250 2.1.5 Ok")
            elif upper == "DATA":
                data_mode = True
                data_lines.clear()
                self.send_line("354 End data with <CR><LF>.<CR><LF>")
            elif upper == "RSET":
                mail_from = ""
                rcpt_to.clear()
                data_mode = False
                data_lines.clear()
                self.send_line("250 2.0.0 Ok")
            elif upper == "NOOP":
                self.send_line("250 2.0.0 Ok")
            elif upper == "QUIT":
                self.send_line("221 2.0.0 Bye")
                break
            else:
                self.send_line("250 2.0.0 Ok")


class _MailQueryHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if path in ("/api/v1/search", "/api/v1/messages"):
            target_to = ""
            query_str = query.get("query", [""])[0]
            if query_str.lower().startswith("to:"):
                target_to = query_str[3:].strip()
            elif query_str:
                target_to = query_str.strip()
            matches = get_messages(to=target_to if target_to else None)
            self._send_json({"messages": matches, "total": len(matches)})
        elif path.startswith("/api/v1/message/"):
            msg_id = path.split("/")[-1]
            with _LOCK:
                matched = next((m for m in _MESSAGES if str(m["ID"]) == msg_id), None)
            if matched:
                self._send_json(matched)
            else:
                self.send_response(404)
                self.end_headers()
        else:
            self._send_json({"status": "ok", "messages": len(_MESSAGES)})

    def do_DELETE(self) -> None:  # noqa: N802
        clear_messages()
        self._send_json({"status": "cleared"})

    def _send_json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)


class TestSMTPServer:
    def __init__(
        self, host: str = "127.0.0.1", smtp_port: int = 1025, http_port: int = 8025
    ) -> None:
        self.host = host
        self.smtp_port = smtp_port
        self.http_port = http_port
        self.smtp_sock: socket.socket | None = None
        self.http_srv: HTTPServer | None = None
        self.running = False
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.running = True
        try:
            self.smtp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.smtp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.smtp_sock.bind((self.host, self.smtp_port))
            self.smtp_sock.listen(10)
            self.smtp_sock.settimeout(1.0)
        except OSError as exc:
            logger.warning("SMTP test socket bind warning on port %d: %s", self.smtp_port, exc)
            return

        def _smtp_worker() -> None:
            while self.running and self.smtp_sock:
                try:
                    conn, _ = self.smtp_sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                try:
                    _SMTPHandler(conn).handle()
                except Exception:
                    pass
                finally:
                    with contextlib.suppress(OSError):
                        conn.close()

        smtp_t = threading.Thread(target=_smtp_worker, daemon=True)
        smtp_t.start()
        self._threads.append(smtp_t)

        try:
            self.http_srv = HTTPServer((self.host, self.http_port), _MailQueryHandler)
            http_t = threading.Thread(target=self.http_srv.serve_forever, daemon=True)
            http_t.start()
            self._threads.append(http_t)
        except OSError as exc:
            logger.warning("HTTP query server bind warning on port %d: %s", self.http_port, exc)

    def stop(self) -> None:
        self.running = False
        if self.smtp_sock:
            with contextlib.suppress(OSError):
                self.smtp_sock.close()
            self.smtp_sock = None
        if self.http_srv:
            with contextlib.suppress(OSError):
                self.http_srv.shutdown()
                self.http_srv.server_close()
            self.http_srv = None


_GLOBAL_SERVER: TestSMTPServer | None = None


def ensure_test_smtp_server(
    host: str = "127.0.0.1", smtp_port: int = 1025, http_port: int = 8025
) -> TestSMTPServer:
    global _GLOBAL_SERVER
    if _GLOBAL_SERVER is None or not _GLOBAL_SERVER.running:
        _GLOBAL_SERVER = TestSMTPServer(host=host, smtp_port=smtp_port, http_port=http_port)
        _GLOBAL_SERVER.start()
    return _GLOBAL_SERVER


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)
    print("Starting standard test SMTP server on 127.0.0.1:1025, query API on 127.0.0.1:8025...")
    server = ensure_test_smtp_server()
    print("Test SMTP server active. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()
        print("Test SMTP server stopped.")
