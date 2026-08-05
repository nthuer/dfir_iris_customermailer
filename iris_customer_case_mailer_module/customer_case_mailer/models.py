"""Data models (plain dataclasses, no IRIS dependencies).

These objects form the interfaces between the services and are
deliberately framework-free so they can be tested without a running
IRIS instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class MailerConfig:
    """Validated module configuration (see config_service.load_config)."""

    smtp_host: str
    smtp_port: int
    smtp_use_tls: bool
    smtp_username: Optional[str]
    smtp_password: Optional[str]
    smtp_from_address: str
    smtp_from_name: Optional[str]
    smtp_timeout_seconds: int
    default_cc: List[str]
    default_bcc: List[str]
    test_mode_enabled: bool
    test_mode_recipients: List[str]
    customer_contact_roles: List[str]     # contact roles addressed, e.g. ["CISO"]
    notes_directory_name: str
    default_subject_template: str
    allowed_report_formats: List[str]
    allowed_report_templates: List[str]   # empty = all investigation templates
    allowed_mail_templates: List[str]     # empty = all templates in the directory
    mail_templates_dir: str
    manual_hook_sends_with_defaults: bool
    default_mail_template: Optional[str]
    default_report_template: Optional[str]
    default_report_format: str

    def secrets(self) -> List[str]:
        """All values that must never appear in logs/notes."""
        return [s for s in (self.smtp_password,) if s]

    def contact_roles_label(self) -> str:
        """Human-readable role list for error messages."""
        return ", ".join(self.customer_contact_roles)


@dataclass(frozen=True)
class CustomerContact:
    """A contact configured on an IRIS customer (IRIS model ``Contact``).

    ``role`` maps to the free-text field "Contact Role" in the IRIS UI;
    recipients are selected by matching it against
    ``MailerConfig.customer_contact_roles``.
    """

    name: str
    email: str
    role: str

    def display(self) -> str:
        """Readable identification for notes and error messages."""
        name = self.name.strip() or "unnamed contact"
        return f"{name} <{self.email.strip()}>" if self.email.strip() else name


@dataclass(frozen=True)
class CaseContext:
    """Case and customer data for templates, reports and notes.

    ``customer_name`` is None when no customer is assigned to the case.
    ``contacts`` holds all contacts configured on that customer; the
    recipient service picks the ones carrying the configured role.
    """

    case_id: int
    name: str
    description: str
    open_date: str
    soc_id: str
    customer_name: Optional[str]
    customer_attributes: Dict = field(default_factory=dict)
    contacts: List[CustomerContact] = field(default_factory=list)

    def template_context(self) -> Dict:
        """Context for mail/subject templates.

        ``case.for_customer`` is mapped to the name of the customer
        (in the IRIS data model this is ``Cases.client`` / ``Client.name``).
        """
        return {
            "case": {
                "name": self.name,
                "description": self.description,
                "open_date": self.open_date,
                "for_customer": self.customer_name or "",
                "soc_id": self.soc_id,
                "customer": {
                    "name": self.customer_name or "",
                    "attributes": self.customer_attributes,
                    "contacts": [
                        {"name": c.name, "email": c.email, "role": c.role}
                        for c in self.contacts
                    ],
                },
            }
        }


@dataclass(frozen=True)
class ResolvedRecipients:
    """Recipients assembled server-side (final)."""

    to: List[str]
    cc: List[str]
    bcc: List[str]
    test_mode_active: bool = False
    # Contacts carrying the configured role whose email address was
    # invalid and therefore skipped – documented in the note and the UI.
    skipped_invalid: List[str] = field(default_factory=list)
    # Original (production) recipients – only relevant for documentation
    # in the note when test mode has replaced them.
    original_to: List[str] = field(default_factory=list)
    original_cc: List[str] = field(default_factory=list)
    original_bcc: List[str] = field(default_factory=list)

    def all_envelope(self) -> List[str]:
        return list(self.to) + list(self.cc) + list(self.bcc)


@dataclass(frozen=True)
class SendSelection:
    """Analyst selection from the dialog (validated server-side)."""

    mail_template: str
    report_template: str          # template name or id (as string)
    report_format: str            # "docx" | "html"
    subject_override: Optional[str] = None  # final subject if edited
    test_send: bool = False       # explicit test send from the dialog


@dataclass(frozen=True)
class ReportArtifact:
    """Rendered investigation report used as mail/note attachment."""

    filename: str
    content: bytes
    mimetype: str
    report_format: str
    template_name: str


@dataclass(frozen=True)
class SendResult:
    """Result of a send attempt, for UI and hook."""

    success: bool
    message: str
    note_id: Optional[int] = None
    note_title: Optional[str] = None
