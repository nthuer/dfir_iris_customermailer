"""Tests: SMTP sending (auth, TLS, error mapping, MIME structure)."""

import smtplib

import pytest

from iris_customer_case_mailer_module.customer_case_mailer import smtp_service as smtp_mod
from iris_customer_case_mailer_module.customer_case_mailer.audit_service import get_logger
from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.errors import (
    SmtpAuthError,
    SmtpConnectError,
)
from iris_customer_case_mailer_module.customer_case_mailer.models import (
    ReportArtifact,
    ResolvedRecipients,
)


class FakeSMTP:
    """Records SMTP calls; replaces smtplib.SMTP/SMTP_SSL."""

    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.tls_context = context
        self.starttls_called = False
        self.login_args = None
        self.sent = []
        self.login_exception = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context=None):
        self.starttls_called = True

    def login(self, username, password):
        if self.login_exception:
            raise self.login_exception
        self.login_args = (username, password)

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent.append({"msg": msg, "from": from_addr, "to": to_addrs})


@pytest.fixture(autouse=True)
def patched_smtplib(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtp_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtp_mod.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def recipients():
    return ResolvedRecipients(to=["customer@example.org"],
                              cc=["lead@example.org"],
                              bcc=["archive@example.org"])


@pytest.fixture
def artifact():
    return ReportArtifact(filename="report.docx", content=b"PK",
                          mimetype=("application/vnd.openxmlformats-officedocument"
                                    ".wordprocessingml.document"),
                          report_format="docx", template_name="Standard")


def _service(raw_config, **overrides):
    raw_config.update(overrides)
    config = load_config(raw_config)
    return smtp_mod.SmtpService(config, get_logger(secrets=config.secrets()))


def test_send_without_auth(raw_config, recipients, artifact):
    _service(raw_config).send(recipients, "Subject", "<p>Hi</p>", artifact)
    smtp = FakeSMTP.instances[0]
    assert smtp.login_args is None          # no login without credentials
    assert len(smtp.sent) == 1


def test_send_with_auth(raw_config, recipients, artifact):
    _service(raw_config, smtp_username="svc", smtp_password="secret").send(
        recipients, "Subject", "<p>Hi</p>", artifact)
    assert FakeSMTP.instances[0].login_args == ("svc", "secret")


def test_tls_enabled_uses_starttls(raw_config, recipients, artifact):
    _service(raw_config, smtp_use_tls=True).send(recipients, "S", "<p>x</p>", artifact)
    assert FakeSMTP.instances[0].starttls_called is True


def test_tls_disabled_skips_starttls(raw_config, recipients, artifact):
    _service(raw_config, smtp_use_tls=False).send(recipients, "S", "<p>x</p>", artifact)
    assert FakeSMTP.instances[0].starttls_called is False


def test_auth_failure_maps_to_smtp_auth_error(raw_config, recipients, artifact,
                                              monkeypatch):
    service = _service(raw_config, smtp_username="svc", smtp_password="wrong")
    original_init = FakeSMTP.__init__

    def failing_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.login_exception = smtplib.SMTPAuthenticationError(535, b"denied")

    monkeypatch.setattr(FakeSMTP, "__init__", failing_init)
    with pytest.raises(SmtpAuthError):
        service.send(recipients, "S", "<p>x</p>", artifact)


def test_connect_failure_maps_to_connect_error(raw_config, recipients, artifact,
                                               monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(smtp_mod.smtplib, "SMTP", refuse)
    with pytest.raises(SmtpConnectError):
        _service(raw_config).send(recipients, "S", "<p>x</p>", artifact)


def test_bcc_only_in_envelope(raw_config, recipients, artifact):
    _service(raw_config).send(recipients, "Subject", "<p>Hi</p>", artifact)
    sent = FakeSMTP.instances[0].sent[0]
    msg = sent["msg"]
    assert "archive@example.org" in sent["to"]          # envelope
    assert msg["Bcc"] is None                           # no header
    assert msg["To"] == "customer@example.org"
    assert msg["Cc"] == "lead@example.org"


def test_message_contains_html_and_attachment(raw_config, recipients, artifact):
    _service(raw_config).send(recipients, "Subject", "<p>Hello customer</p>", artifact)
    msg = FakeSMTP.instances[0].sent[0]["msg"]
    parts = {part.get_content_type() for part in msg.walk()}
    assert "text/html" in parts
    attachment = [p for p in msg.walk()
                  if p.get_filename() == "report.docx"]
    assert len(attachment) == 1
