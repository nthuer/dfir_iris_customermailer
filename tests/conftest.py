"""Test fixtures: FakeAdapter (instead of IRIS) and fake SMTP.

The domain logic is fully testable without a running IRIS because all
IRIS access goes through the adapter, which is replaced here by an
in-memory fake.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iris_customer_case_mailer_module.customer_case_mailer.errors import (  # noqa: E402
    AttachmentError,
    NotesError,
)
from iris_customer_case_mailer_module.customer_case_mailer.mailer import (  # noqa: E402
    CustomerCaseMailer,
)
from iris_customer_case_mailer_module.customer_case_mailer.models import (  # noqa: E402
    CaseContext,
    CustomerContact,
)

MAIL_TEMPLATE = """<html><body>
<h1>{{ case.name }}</h1>
<p>Customer: {{ case.for_customer }}</p>
<p>SOC: {{ case.soc_id }} | Opened: {{ case.open_date }}</p>
<p>{{ case.description }}</p>
</body></html>"""

BROKEN_TEMPLATE = "<html>{{ case.does_not_exist }}</html>"


def contact(name: str, email: str, role: str = "CISO") -> CustomerContact:
    """Shorthand for building customer contacts in tests."""
    return CustomerContact(name=name, email=email, role=role)


DEFAULT_CONTACTS = [
    contact("Jane Doe", "ciso@example.org", "CISO"),
    contact("John Ops", "soc-lead@example.org", "SOC Lead"),
]


class FakeAdapter:
    """In-memory replacement for IrisAdapter."""

    def __init__(self,
                 contacts: Optional[List[CustomerContact]] = None,
                 customer_name: Optional[str] = "ACME Corp"):
        self.contacts = list(DEFAULT_CONTACTS if contacts is None else contacts)
        self.customer_name = customer_name
        self.report_templates = [
            {"id": 1, "name": "Standard Investigation", "description": "", "format": "docx"},
            {"id": 2, "name": "HTML Investigation", "description": "", "format": "html"},
        ]
        self.report_bytes = {"docx": b"PK-DOCX-REPORT", "html": b"<html>REPORT</html>"}
        # Recordings
        self.directories: Dict = {}          # (case_id, name) -> id
        self.created_directories: List = []
        self.notes: List[Dict] = []
        self.datastore_files: List[Dict] = []
        # Failure switches
        self.fail_directory = False
        self.fail_note = False
        self.fail_datastore = False

    # ---- Case ----
    def get_case_context(self, case_id: int) -> CaseContext:
        return CaseContext(
            case_id=case_id,
            name="Ransomware investigation",
            description="Suspected encryption in the file server cluster.",
            open_date="2026-07-01",
            soc_id="SOC-2026-0042",
            customer_name=self.customer_name,
            customer_attributes={},
            contacts=list(self.contacts),
        )

    def get_customer_contacts(self, client_id: int) -> List[CustomerContact]:
        return list(self.contacts)

    def get_user_display(self, user_id):
        return f"analyst (id {user_id})"

    # ---- Reports ----
    def list_investigation_report_templates(self):
        return list(self.report_templates)

    def generate_report(self, case_id, template_id, report_format, user_id):
        return self.report_bytes[report_format]

    # ---- Notes / Datastore ----
    def ensure_note_directory(self, case_id, name, user_id):
        if self.fail_directory:
            raise NotesError(f"Note directory '{name}' could not be created.")
        key = (case_id, name)
        if key not in self.directories:
            self.directories[key] = 100 + len(self.directories)
            self.created_directories.append(key)
        return self.directories[key]

    def add_note(self, case_id, directory_id, title, content, user_id):
        if self.fail_note:
            raise NotesError("Note could not be saved.")
        note = {"case_id": case_id, "directory_id": directory_id,
                "title": title, "content": content, "user_id": user_id,
                "note_id": 500 + len(self.notes)}
        self.notes.append(note)
        return note["note_id"]

    def store_report_in_datastore(self, case_id, filename, content, user_id):
        if self.fail_datastore:
            raise AttachmentError("Report could not be stored in the datastore.")
        file_id = 900 + len(self.datastore_files)
        self.datastore_files.append({"case_id": case_id, "filename": filename,
                                     "content": content, "file_id": file_id})
        return file_id, f"/datastore/file/view/{file_id}?cid={case_id}"


class FakeSmtpService:
    """Replacement for SmtpService in flow tests."""

    def __init__(self, exception: Optional[Exception] = None):
        self.exception = exception
        self.sent: List[Dict] = []

    def send(self, recipients, subject, html_body, artifact):
        if self.exception is not None:
            raise self.exception
        self.sent.append({"recipients": recipients, "subject": subject,
                          "html_body": html_body, "artifact": artifact})


@pytest.fixture
def mail_templates_dir(tmp_path):
    directory = tmp_path / "mail_templates"
    directory.mkdir()
    (directory / "standard.html").write_text(MAIL_TEMPLATE, encoding="utf-8")
    (directory / "broken.html").write_text(BROKEN_TEMPLATE, encoding="utf-8")
    return directory


@pytest.fixture
def raw_config(mail_templates_dir):
    return {
        "smtp_host": "smtp.example.org",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_username": None,
        "smtp_password": None,
        "smtp_from_address": "soc@example.org",
        "smtp_from_name": "Example SOC",
        "smtp_timeout_seconds": 5,
        "default_cc": "lead@example.org",
        "default_bcc": "archive@example.org",
        "test_mode_enabled": False,
        "test_mode_recipients": "test@example.org",
        "customer_contact_roles": "CISO",
        "notes_directory_name": "Communication",
        "default_subject_template": "Report – {{ case.name }} ({{ case.soc_id }})",
        "allowed_report_formats": "docx,html",
        "mail_templates_dir": str(mail_templates_dir),
        "default_mail_template": "standard.html",
        "default_report_template": "1",
        "default_report_format": "docx",
    }


@pytest.fixture
def fake_adapter():
    return FakeAdapter()


@pytest.fixture
def fake_smtp():
    return FakeSmtpService()


@pytest.fixture
def mailer(raw_config, fake_adapter, fake_smtp):
    return CustomerCaseMailer(raw_config, adapter=fake_adapter,
                              smtp_service=fake_smtp)
