"""Tests: notes (directory creation, note content, attachment, masking)."""

from datetime import datetime, timezone

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.audit_service import get_logger
from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.errors import NotesError
from iris_customer_case_mailer_module.customer_case_mailer.models import (
    ReportArtifact,
    ResolvedRecipients,
    SendSelection,
)
from iris_customer_case_mailer_module.customer_case_mailer.notes_service import (
    NotesService,
    build_note_title,
)

TS = datetime(2026, 7, 17, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def service(raw_config, fake_adapter):
    config = load_config(raw_config)
    return NotesService(config, fake_adapter, get_logger(secrets=config.secrets()))


@pytest.fixture
def case_ctx(fake_adapter):
    return fake_adapter.get_case_context(1)


@pytest.fixture
def recipients():
    return ResolvedRecipients(to=["customer@example.org"], cc=["lead@example.org"],
                              bcc=["archive@example.org"])


@pytest.fixture
def selection():
    return SendSelection(mail_template="standard.html", report_template="1",
                         report_format="docx")


@pytest.fixture
def artifact():
    return ReportArtifact(filename="report.docx", content=b"PK", mimetype="application/x",
                          report_format="docx", template_name="Standard")


def test_note_title_formats():
    assert build_note_title(TS, "ACME Corp", True) == "Customer mail 2026-07-17 09:30 – ACME Corp"
    assert build_note_title(TS, "ACME Corp", False) == \
        "FAILED – Customer mail 2026-07-17 09:30 – ACME Corp"
    assert "Unknown customer" in build_note_title(TS, None, True)


def test_directory_created_automatically(service, case_ctx, recipients,
                                         selection, artifact, fake_adapter):
    service.create_send_note(case_ctx, 7, "analyst (id 7)", recipients,
                             "Subject", "<p>x</p>", selection, artifact,
                             success=True, timestamp=TS)
    assert (1, "Communication") in fake_adapter.created_directories


def test_directory_reused(service, case_ctx, recipients, selection, artifact,
                          fake_adapter):
    for _ in range(2):
        service.create_send_note(case_ctx, 7, "a", recipients, "S", "<p>x</p>",
                                 selection, artifact, success=True, timestamp=TS)
    assert fake_adapter.created_directories.count((1, "Communication")) == 1


def test_note_contains_metadata_and_body(service, case_ctx, recipients,
                                         selection, artifact, fake_adapter):
    note_id, title = service.create_send_note(
        case_ctx, 7, "analyst (id 7)", recipients, "Important subject",
        "<p>Hello customer</p>", selection, artifact, success=True, timestamp=TS)
    note = fake_adapter.notes[0]
    assert note["note_id"] == note_id and note["title"] == title
    content = note["content"]
    assert "2026-07-17 09:30" in content
    assert "customer@example.org" in content
    assert "lead@example.org" in content and "archive@example.org" in content
    assert "Important subject" in content
    assert "analyst (id 7)" in content
    assert "<p>Hello customer</p>" in content


def test_report_attached_to_note(service, case_ctx, recipients, selection,
                                 artifact, fake_adapter):
    service.create_send_note(case_ctx, 7, "a", recipients, "S", "<p>x</p>",
                             selection, artifact, success=True, timestamp=TS)
    stored = fake_adapter.datastore_files[0]
    assert stored["filename"] == "report.docx" and stored["content"] == b"PK"
    assert f"/datastore/file/view/{stored['file_id']}?cid=1" in \
        fake_adapter.notes[0]["content"]


def test_attachment_failure_documented_not_fatal(service, case_ctx, recipients,
                                                 selection, artifact, fake_adapter):
    fake_adapter.fail_datastore = True
    service.create_send_note(case_ctx, 7, "a", recipients, "S", "<p>x</p>",
                             selection, artifact, success=True, timestamp=TS)
    assert len(fake_adapter.notes) == 1
    assert "COULD NOT BE ATTACHED" in fake_adapter.notes[0]["content"]


def test_directory_failure_raises(service, case_ctx, recipients, selection,
                                  artifact, fake_adapter):
    fake_adapter.fail_directory = True
    with pytest.raises(NotesError):
        service.create_send_note(case_ctx, 7, "a", recipients, "S", "<p>x</p>",
                                 selection, artifact, success=True, timestamp=TS)


def test_secret_never_written_to_note(raw_config, fake_adapter, case_ctx,
                                      recipients, selection, artifact):
    raw_config["smtp_username"] = "svc"
    raw_config["smtp_password"] = "SuperSecret123"
    config = load_config(raw_config)
    service = NotesService(config, fake_adapter, get_logger(secrets=config.secrets()))
    service.create_send_note(
        case_ctx, 7, "a", recipients, "S", "<p>x</p>", selection, artifact,
        success=False, timestamp=TS,
        error_message="SMTP login failed with password SuperSecret123")
    content = fake_adapter.notes[0]["content"]
    assert "SuperSecret123" not in content
    assert "********" in content
