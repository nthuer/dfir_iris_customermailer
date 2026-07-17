"""Tests: complete send data flow (preview, success, failure cases)."""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.errors import SmtpConnectError
from iris_customer_case_mailer_module.customer_case_mailer.mailer import CustomerCaseMailer
from iris_customer_case_mailer_module.customer_case_mailer.models import SendSelection

from conftest import FakeSmtpService

SELECTION = SendSelection(mail_template="standard.html", report_template="1",
                          report_format="docx")


def test_dialog_state(mailer):
    state = mailer.dialog_state(1)
    assert state["recipients_valid"] is True
    assert state["to"] == ["customer@example.org"]
    assert state["cc"] == ["lead@example.org"]
    assert state["bcc"] == ["archive@example.org"]
    assert "standard.html" in state["mail_templates"]
    assert {t["name"] for t in state["report_templates"]} == \
        {"Standard Investigation", "HTML Investigation"}
    assert "Ransomware investigation" in state["default_subject"]


def test_mail_preview(mailer):
    preview = mailer.preview_mail(1, SELECTION)
    assert "Ransomware investigation" in preview["subject"]
    assert "SOC-2026-0042" in preview["subject"]
    assert "ACME Corp" in preview["body_html"]


def test_report_preview(mailer):
    artifact = mailer.preview_report(1, SELECTION, user_id=7)
    assert artifact.content == b"PK-DOCX-REPORT"
    assert artifact.filename.endswith(".docx")


def test_successful_send_creates_exactly_one_note(mailer, fake_adapter, fake_smtp):
    result = mailer.send(1, 7, "analyst (id 7)", SELECTION)
    assert result.success is True
    assert len(fake_smtp.sent) == 1
    assert len(fake_adapter.notes) == 1
    note = fake_adapter.notes[0]
    assert note["title"].startswith("Customer mail ")
    assert note["title"].endswith("– ACME Corp")
    assert result.note_id == note["note_id"]
    # The report is attached to the note as a real file (datastore link in content).
    assert len(fake_adapter.datastore_files) == 1
    assert fake_adapter.datastore_files[0]["content"] == b"PK-DOCX-REPORT"


def test_smtp_failure_creates_failure_note(raw_config, fake_adapter):
    smtp = FakeSmtpService(exception=SmtpConnectError("SMTP server is not reachable."))
    mailer = CustomerCaseMailer(raw_config, adapter=fake_adapter, smtp_service=smtp)
    result = mailer.send(1, 7, "analyst (id 7)", SELECTION)
    assert result.success is False
    assert "not reachable" in result.message
    note = fake_adapter.notes[0]
    assert note["title"].startswith("FAILED – Customer mail ")
    assert "not reachable" in note["content"]


def test_template_error_stops_send_and_creates_note(mailer, fake_adapter, fake_smtp):
    broken = SendSelection(mail_template="broken.html", report_template="1",
                           report_format="docx")
    result = mailer.send(1, 7, "analyst", broken)
    assert result.success is False
    assert len(fake_smtp.sent) == 0                 # sending was stopped
    assert fake_adapter.notes[0]["title"].startswith("FAILED")


def test_invalid_recipient_stops_send(raw_config, fake_adapter, fake_smtp):
    fake_adapter.contact_emails = "not-an-email-address"
    mailer = CustomerCaseMailer(raw_config, adapter=fake_adapter,
                                smtp_service=fake_smtp)
    result = mailer.send(1, 7, "analyst", SELECTION)
    assert result.success is False
    assert len(fake_smtp.sent) == 0
    assert "Invalid email address" in result.message
    assert len(fake_adapter.notes) == 1


def test_missing_customer_stops_send(raw_config, fake_adapter, fake_smtp):
    fake_adapter.customer_name = None
    mailer = CustomerCaseMailer(raw_config, adapter=fake_adapter,
                                smtp_service=fake_smtp)
    result = mailer.send(1, 7, "analyst", SELECTION)
    assert result.success is False
    assert "No customer" in result.message
    assert "Unknown customer" in fake_adapter.notes[0]["title"]


def test_test_mode_send(raw_config, fake_adapter, fake_smtp):
    raw_config["test_mode_enabled"] = True
    raw_config["test_mode_recipients"] = "qa@example.org"
    mailer = CustomerCaseMailer(raw_config, adapter=fake_adapter,
                                smtp_service=fake_smtp)
    result = mailer.send(1, 7, "analyst", SELECTION)
    assert result.success is True
    sent = fake_smtp.sent[0]
    assert sent["recipients"].to == ["qa@example.org"]
    assert sent["recipients"].cc == [] and sent["recipients"].bcc == []
    assert sent["subject"].startswith("[TEST MODE] ")
    content = fake_adapter.notes[0]["content"]
    assert "Test mode" in content
    assert "customer@example.org" in content        # production recipients documented


def test_subject_override(mailer, fake_smtp):
    selection = SendSelection(mail_template="standard.html", report_template="1",
                              report_format="docx",
                              subject_override="Custom subject")
    mailer.send(1, 7, "analyst", selection)
    assert fake_smtp.sent[0]["subject"] == "Custom subject"


def test_note_failure_after_successful_send_is_reported(raw_config, fake_adapter,
                                                        fake_smtp):
    fake_adapter.fail_note = True
    mailer = CustomerCaseMailer(raw_config, adapter=fake_adapter,
                                smtp_service=fake_smtp)
    result = mailer.send(1, 7, "analyst", SELECTION)
    # The mail is out -> success, but the analyst learns about the docs problem.
    assert result.success is True
    assert "note" in result.message
