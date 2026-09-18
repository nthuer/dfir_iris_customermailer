"""Central error classes of the customer_case_mailer module.

All domain errors inherit from :class:`MailerError` and carry a
user-facing message (``user_message``) that is safe to display in the
UI and in notes (no secrets, no stack traces).

Technical details (e.g. the original exception) belong in ``details``
and are only logged – after secret masking.
"""

from __future__ import annotations

from typing import Optional


class MailerError(Exception):
    """Base class of all domain errors of this module."""

    def __init__(self, user_message: str, details: Optional[str] = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.details = details


class ConfigError(MailerError):
    """Module configuration is missing, incomplete or invalid."""


class AdapterError(MailerError):
    """IRIS internals unavailable or unexpected behaviour of the IRIS API."""


class RecipientError(MailerError):
    """Recipients cannot be determined (customer/attribute/validation)."""


class TemplateRenderError(MailerError):
    """Mail template (subject or body) cannot be rendered. Hard blocker."""


class ReportRenderError(MailerError):
    """Investigation report template cannot be rendered. Hard blocker."""


class PreviewRequiredError(MailerError):
    """No preview note matches the content that would be sent."""


class NotesError(MailerError):
    """Note directory or note cannot be created/saved."""


class AttachmentError(NotesError):
    """Report cannot be attached to the note as a file."""


class SmtpError(MailerError):
    """Base class of all SMTP errors."""


class SmtpConnectError(SmtpError):
    """SMTP host not reachable."""


class SmtpAuthError(SmtpError):
    """SMTP authentication failed."""


class SmtpTlsError(SmtpError):
    """TLS negotiation (STARTTLS/SSL) failed."""


class SmtpTimeoutError(SmtpError):
    """Timeout while connecting or sending."""


class SmtpSendError(SmtpError):
    """Server rejected the mail or the transmission failed."""
