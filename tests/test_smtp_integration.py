"""Integration test: real SMTP delivery over a real socket.

The other SMTP tests replace smtplib with a fake. This one runs the
actual protocol conversation against a minimal in-process SMTP server,
so the message that a mail server would really receive is asserted -
envelope, headers and attachment included.

No network access: the server binds to 127.0.0.1 on a free port.
"""

import email
import socket
import threading

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.audit_service import get_logger
from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.smtp_service import SmtpService
from iris_customer_case_mailer_module.customer_case_mailer.models import (
    ReportArtifact,
    ResolvedRecipients,
)

RECIPIENT = "customer.ciso@example.org"


class TinySmtpServer(threading.Thread):
    """Speaks just enough SMTP for smtplib to deliver one message."""

    daemon = True

    def __init__(self):
        super().__init__()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(15)
        self.port = self._sock.getsockname()[1]
        self.mail_from = None
        self.rcpt_to = []
        self.data = b""
        self.error = None

    def run(self):
        try:
            conn, _ = self._sock.accept()
            conn.settimeout(15)
            reader = conn.makefile("rb")
            conn.sendall(b"220 tiny.test ESMTP\r\n")
            while True:
                line = reader.readline()
                if not line:
                    break
                command = line.decode("utf-8", "replace").strip()
                upper = command.upper()
                if upper.startswith("EHLO"):
                    conn.sendall(b"250-tiny.test\r\n250 HELP\r\n")
                elif upper.startswith("HELO"):
                    conn.sendall(b"250 tiny.test\r\n")
                elif upper.startswith("MAIL FROM:"):
                    self.mail_from = command.split(":", 1)[1].strip().strip("<>")
                    conn.sendall(b"250 OK\r\n")
                elif upper.startswith("RCPT TO:"):
                    self.rcpt_to.append(command.split(":", 1)[1].strip().strip("<>"))
                    conn.sendall(b"250 OK\r\n")
                elif upper == "DATA":
                    conn.sendall(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                    chunks = []
                    for raw in reader:
                        if raw in (b".\r\n", b".\n"):
                            break
                        # undo the dot-stuffing smtplib applies
                        chunks.append(raw[1:] if raw.startswith(b"..") else raw)
                    self.data = b"".join(chunks)
                    conn.sendall(b"250 OK: queued\r\n")
                elif upper == "QUIT":
                    conn.sendall(b"221 Bye\r\n")
                    break
                else:
                    conn.sendall(b"250 OK\r\n")
            conn.close()
        except Exception as exc:  # noqa: BLE001 - surfaced in the test
            self.error = exc
        finally:
            self._sock.close()


@pytest.fixture
def smtp_server():
    server = TinySmtpServer()
    server.start()
    yield server
    server.join(timeout=20)
    assert not server.is_alive(), "SMTP test server did not terminate"


def test_real_smtp_delivery_with_report_attachment(raw_config, smtp_server):
    raw_config.update({
        "smtp_host": "127.0.0.1",
        "smtp_port": smtp_server.port,
        "smtp_use_tls": False,          # the tiny server speaks no TLS
        "smtp_username": None,
        "smtp_password": None,
        "smtp_from_address": "soc@example.org",
        "smtp_from_name": "Example SOC",
    })
    config = load_config(raw_config)
    service = SmtpService(config, get_logger(secrets=config.secrets()))

    recipients = ResolvedRecipients(to=[RECIPIENT], cc=["lead@example.org"],
                                    bcc=["archive@example.org"])
    artifact = ReportArtifact(
        filename="investigation_report.docx", content=b"PK\x03\x04docx-bytes",
        mimetype=("application/vnd.openxmlformats-officedocument"
                  ".wordprocessingml.document"),
        report_format="docx", template_name="Standard Investigation")

    service.send(recipients, "Investigation Report – ACME (SOC-1)",
                 "<html><body><p>Dear Sir or Madam,</p></body></html>", artifact)

    smtp_server.join(timeout=20)
    assert smtp_server.error is None, f"SMTP server failed: {smtp_server.error}"

    # Envelope: everyone gets it, including BCC.
    assert smtp_server.mail_from == "soc@example.org"
    assert sorted(smtp_server.rcpt_to) == sorted(
        [RECIPIENT, "lead@example.org", "archive@example.org"])

    message = email.message_from_bytes(smtp_server.data)
    assert message["To"] == RECIPIENT
    assert message["Cc"] == "lead@example.org"
    assert message["Bcc"] is None          # BCC must never be in the headers
    assert message["From"] == "Example SOC <soc@example.org>"
    # Non-ASCII subjects are RFC 2047 encoded; decode before comparing.
    assert str(email.header.make_header(email.header.decode_header(message["Subject"]))) \
        == "Investigation Report – ACME (SOC-1)"

    parts = {p.get_content_type(): p for p in message.walk()
             if p.get_content_maintype() != "multipart"}
    assert "text/plain" in parts                       # fallback for plain-text clients
    assert "Dear Sir or Madam" in parts["text/html"].get_payload(decode=True).decode()

    attachments = [p for p in message.walk() if p.get_filename()]
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "investigation_report.docx"
    assert attachments[0].get_payload(decode=True) == b"PK\x03\x04docx-bytes"
