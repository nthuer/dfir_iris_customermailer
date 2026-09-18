"""Tests: preview and send flows (preview gate, success, failure cases)."""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.errors import SmtpConnectError
from iris_customer_case_mailer_module.customer_case_mailer.mailer import CustomerCaseMailer

from conftest import FakeSmtpService, contact


def _mailer(raw_config, adapter, smtp=None):
    return CustomerCaseMailer(raw_config, adapter=adapter,
                              smtp_service=smtp or FakeSmtpService())


def _titles(adapter):
    return [n["title"] for n in adapter.notes]


# ------------------------------------------------------------------ preview

def test_preview_creates_preview_note_and_sends_nothing(mailer, fake_adapter, fake_smtp):
    result = mailer.preview(1, 7, "analyst (id 7)")
    assert result.success is True
    assert fake_smtp.sent == []
    assert len(fake_adapter.notes) == 1
    note = fake_adapter.notes[0]
    assert note["title"].startswith("PREVIEW – Customer mail ")
    assert note["title"].endswith("– ACME Corp")
    # The preview shows exactly what would be sent.
    assert "ciso@example.org" in note["content"]
    assert "Ransomware investigation" in note["content"]
    assert "ACME Corp" in note["content"]          # rendered mail body
    assert "Preview fingerprint:" in note["content"]
    # ... and the rendered report is attached as a real file.
    assert fake_adapter.datastore_files[0]["content"] == b"PK-DOCX-REPORT"


def test_preview_failure_creates_preview_failed_note(raw_config, fake_adapter):
    fake_adapter.contacts = [contact("John Ops", "soc@example.org", "SOC Lead")]
    result = _mailer(raw_config, fake_adapter).preview(1, 7, "analyst")
    assert result.success is False
    assert "no contact with role 'CISO'" in result.message
    assert fake_adapter.notes[0]["title"].startswith("PREVIEW FAILED – ")
    assert "no contact with role 'CISO'" in fake_adapter.notes[0]["content"]


def test_template_error_fails_preview(mailer, fake_adapter):
    fake_adapter.send_options = {"mail_template": "broken.html"}
    result = mailer.preview(1, 7, "analyst")
    assert result.success is False
    assert fake_adapter.notes[0]["title"].startswith("PREVIEW FAILED")


# ------------------------------------------------------------- preview gate

def test_send_without_preview_is_blocked(mailer, fake_adapter, fake_smtp):
    result = mailer.send(1, 7, "analyst")
    assert result.success is False
    assert "No matching preview found" in result.message
    assert fake_smtp.sent == []
    assert _titles(fake_adapter)[0].startswith("FAILED – Customer mail ")


def test_send_after_preview_succeeds(mailer, fake_adapter, fake_smtp):
    mailer.preview(1, 7, "analyst")
    result = mailer.send(1, 7, "analyst")
    assert result.success is True
    assert len(fake_smtp.sent) == 1
    assert fake_smtp.sent[0]["recipients"].to == ["ciso@example.org"]
    # One preview note + exactly one note for the send attempt.
    titles = _titles(fake_adapter)
    assert len(titles) == 2
    assert titles[1].startswith("Customer mail ") and titles[1].endswith("– ACME Corp")
    assert result.note_id == fake_adapter.notes[1]["note_id"]


def test_changed_case_data_invalidates_preview(mailer, fake_adapter, fake_smtp):
    mailer.preview(1, 7, "analyst")
    fake_adapter.case_description = "Updated findings."   # mail body changes
    result = mailer.send(1, 7, "analyst")
    assert result.success is False
    assert fake_smtp.sent == []


def test_changed_recipients_invalidate_preview(mailer, fake_adapter, fake_smtp):
    mailer.preview(1, 7, "analyst")
    fake_adapter.contacts = fake_adapter.contacts + [contact("New", "ciso2@example.org")]
    assert mailer.send(1, 7, "analyst").success is False
    assert fake_smtp.sent == []


def test_changed_send_options_invalidate_preview(mailer, fake_adapter, fake_smtp):
    mailer.preview(1, 7, "analyst")
    fake_adapter.send_options = {"subject": "Different subject"}
    assert mailer.send(1, 7, "analyst").success is False


def test_preview_gate_can_be_disabled(raw_config, fake_adapter, fake_smtp):
    raw_config["require_preview_before_send"] = False
    result = _mailer(raw_config, fake_adapter, fake_smtp).send(1, 7, "analyst")
    assert result.success is True
    assert len(fake_smtp.sent) == 1


def test_test_send_needs_no_preview(mailer, fake_adapter, fake_smtp):
    result = mailer.send(1, 7, "analyst", test_send=True)
    assert result.success is True
    sent = fake_smtp.sent[0]
    assert sent["recipients"].to == ["test@example.org"]
    assert sent["recipients"].cc == [] and sent["recipients"].bcc == []
    assert sent["subject"].startswith("[TEST MODE] ")


def test_global_test_mode_needs_no_preview(raw_config, fake_adapter, fake_smtp):
    raw_config["test_mode_enabled"] = True
    raw_config["test_mode_recipients"] = "qa@example.org"
    result = _mailer(raw_config, fake_adapter, fake_smtp).send(1, 7, "analyst")
    assert result.success is True
    assert fake_smtp.sent[0]["recipients"].to == ["qa@example.org"]
    content = fake_adapter.notes[0]["content"]
    assert "Test mode" in content
    assert "ciso@example.org" in content            # production recipients documented


# ------------------------------------------------------------ send options

def test_case_send_options_override_module_defaults(mailer, fake_adapter, fake_smtp):
    fake_adapter.send_options = {"report_template": "HTML Investigation",
                                 "report_format": "html",
                                 "subject": "Custom subject"}
    mailer.preview(1, 7, "analyst")
    assert mailer.send(1, 7, "analyst").success is True
    sent = fake_smtp.sent[0]
    assert sent["subject"] == "Custom subject"
    assert sent["artifact"].report_format == "html"
    assert sent["artifact"].content == b"<html>REPORT</html>"


def test_missing_report_template_is_reported(raw_config, fake_adapter):
    raw_config["default_report_template"] = None
    result = _mailer(raw_config, fake_adapter).preview(1, 7, "analyst")
    assert result.success is False
    assert "No investigation report template selected" in result.message
    assert fake_adapter.notes[0]["title"].startswith("PREVIEW FAILED")


def test_invalid_report_format_on_case_is_reported(mailer, fake_adapter):
    fake_adapter.send_options = {"report_format": "pdf"}
    result = mailer.preview(1, 7, "analyst")
    assert result.success is False
    assert "pdf" in result.message


# -------------------------------------------------------------- failures

def test_smtp_failure_creates_failure_note(raw_config, fake_adapter):
    smtp = FakeSmtpService(exception=SmtpConnectError("SMTP server is not reachable."))
    mailer = _mailer(raw_config, fake_adapter, smtp)
    mailer.preview(1, 7, "analyst")
    result = mailer.send(1, 7, "analyst")
    assert result.success is False
    assert "not reachable" in result.message
    note = fake_adapter.notes[-1]
    assert note["title"].startswith("FAILED – Customer mail ")
    assert "not reachable" in note["content"]


def test_template_error_stops_send(mailer, fake_adapter, fake_smtp):
    fake_adapter.send_options = {"mail_template": "broken.html"}
    result = mailer.send(1, 7, "analyst")
    assert result.success is False
    assert fake_smtp.sent == []
    assert fake_adapter.notes[0]["title"].startswith("FAILED")


def test_all_ciso_emails_invalid_stops_send(raw_config, fake_adapter, fake_smtp):
    fake_adapter.contacts = [contact("Broken Ciso", "not-an-email")]
    result = _mailer(raw_config, fake_adapter, fake_smtp).send(1, 7, "analyst")
    assert result.success is False
    assert fake_smtp.sent == []
    assert "valid email address" in result.message
    assert len(fake_adapter.notes) == 1


def test_invalid_contact_is_skipped_and_documented(raw_config, fake_adapter, fake_smtp):
    fake_adapter.contacts = [contact("Broken Ciso", "not-an-email"),
                             contact("Jane Doe", "ciso@example.org")]
    mailer = _mailer(raw_config, fake_adapter, fake_smtp)
    mailer.preview(1, 7, "analyst")
    assert mailer.send(1, 7, "analyst").success is True
    assert fake_smtp.sent[0]["recipients"].to == ["ciso@example.org"]
    for note in fake_adapter.notes:     # preview and send note both show it
        assert "Broken Ciso <not-an-email>" in note["content"]


def test_missing_customer_stops_send(raw_config, fake_adapter, fake_smtp):
    fake_adapter.customer_name = None
    result = _mailer(raw_config, fake_adapter, fake_smtp).send(1, 7, "analyst")
    assert result.success is False
    assert "No customer" in result.message
    assert "Unknown customer" in fake_adapter.notes[0]["title"]


def test_note_failure_after_successful_send_is_reported(raw_config, fake_adapter,
                                                        fake_smtp):
    raw_config["require_preview_before_send"] = False
    fake_adapter.fail_note = True
    result = _mailer(raw_config, fake_adapter, fake_smtp).send(1, 7, "analyst")
    # The mail is out -> success, but the analyst learns about the docs problem.
    assert result.success is True
    assert len(fake_smtp.sent) == 1
    assert "note" in result.message
