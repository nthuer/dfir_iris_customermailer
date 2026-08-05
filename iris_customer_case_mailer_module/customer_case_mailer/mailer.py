"""Orchestrator: wires all services into the send data flow.

Data flow of a send attempt (``send``):

    1. Load/validate config              (config_service)
    2. Load case + customer + contacts   (iris_adapter)
    3. Resolve/validate recipients       (recipient_service)
       (contacts with role CISO that carry a valid email address)
    4. Render subject + mail body        (template_service)   -> hard errors
    5. Render investigation report       (report_service)     -> hard errors
    6. Send mail via SMTP                (smtp_service)
    7. Create note in 'Communication',
       attach report as file             (notes_service)
    8. SendResult to UI/hook

If a step fails, the send is aborted, the error is reported to the
analyst AND (best effort) a FAILED note is created containing all data
known up to that point.
"""

from __future__ import annotations

from typing import Dict, Optional

from .audit_service import get_logger, mask_secrets
from .config_service import load_config
from .errors import MailerError, RecipientError
from .iris_adapter import IrisAdapter
from .models import CaseContext, ResolvedRecipients, SendResult, SendSelection
from .notes_service import NotesService
from .recipient_service import resolve_recipients
from .report_service import ReportService
from .smtp_service import SmtpService
from .template_service import TemplateService

TEST_SUBJECT_PREFIX = "[TEST MODE] "


class CustomerCaseMailer:

    def __init__(self, raw_config: Dict, adapter=None, logger=None,
                 smtp_service=None, report_service=None,
                 template_service=None, notes_service=None):
        self.config = load_config(raw_config)
        self.adapter = adapter if adapter is not None else IrisAdapter()
        self.logger = logger or get_logger(secrets=self.config.secrets())
        self.templates = template_service or TemplateService(self.config)
        self.reports = report_service or ReportService(self.config, self.adapter)
        self.smtp = smtp_service or SmtpService(self.config, self.logger)
        self.notes = notes_service or NotesService(self.config, self.adapter, self.logger)

    # ------------------------------------------------------------------ intern

    def _case_context(self, case_id: int) -> CaseContext:
        return self.adapter.get_case_context(case_id)

    def _require_customer(self, ctx: CaseContext) -> None:
        if not ctx.customer_name:
            raise RecipientError(
                "No customer is assigned to this case. Send blocked.")

    def _resolve(self, ctx: CaseContext,
                 force_test_send: bool) -> ResolvedRecipients:
        self._require_customer(ctx)
        return resolve_recipients(ctx.contacts, self.config,
                                  force_test_send=force_test_send)

    def _final_subject(self, ctx: CaseContext,
                       selection: SendSelection,
                       test_mode_active: bool) -> str:
        # The analyst overrides the subject as final text (no re-render –
        # typed { } characters stay untouched).
        if selection.subject_override and selection.subject_override.strip():
            subject = " ".join(selection.subject_override.split())
        else:
            subject = self.templates.render_subject(
                self.config.default_subject_template, ctx.template_context())
        if test_mode_active:
            subject = TEST_SUBJECT_PREFIX + subject
        return subject

    # -------------------------------------------------------------- dialog/API

    def dialog_state(self, case_id: int) -> Dict:
        """Initial state for the send dialog (all determined read-only)."""
        ctx = self._case_context(case_id)
        state: Dict = {
            "case_id": case_id,
            "case_name": ctx.name,
            "customer": ctx.customer_name,
            "test_mode_enabled": self.config.test_mode_enabled,
            "test_send_available": bool(self.config.test_mode_recipients),
            "test_mode_recipients": self.config.test_mode_recipients,
            "cc": self.config.default_cc,
            "bcc": self.config.default_bcc,
            "contact_roles": self.config.customer_contact_roles,
            "mail_templates": self.templates.list_mail_templates(),
            "report_templates": self.reports.list_templates(),
            "report_formats": self.config.allowed_report_formats,
            "default_mail_template": self.config.default_mail_template,
            "default_report_template": self.config.default_report_template,
            "default_report_format": self.config.default_report_format,
            "recipients_valid": False,
            "to": [],
            "skipped_invalid": [],
            "recipient_error": None,
            "default_subject": None,
            "subject_error": None,
        }
        try:
            recipients = self._resolve(ctx, force_test_send=False)
            # The preview always shows the production To recipients; in
            # test mode additionally the effective test recipients.
            state["to"] = recipients.original_to or recipients.to
            state["effective_to"] = recipients.to
            state["effective_cc"] = recipients.cc
            state["effective_bcc"] = recipients.bcc
            state["skipped_invalid"] = recipients.skipped_invalid
            state["recipients_valid"] = True
        except MailerError as exc:
            state["recipient_error"] = exc.user_message
        try:
            state["default_subject"] = self.templates.render_subject(
                self.config.default_subject_template, ctx.template_context())
        except MailerError as exc:
            state["subject_error"] = exc.user_message
        return state

    def preview_mail(self, case_id: int, selection: SendSelection) -> Dict:
        """Preview of subject + rendered HTML body (without sending)."""
        ctx = self._case_context(case_id)
        self._require_customer(ctx)
        test_active = self.config.test_mode_enabled or selection.test_send
        subject = self._final_subject(ctx, selection, test_active)
        body_html = self.templates.render_mail_body(
            selection.mail_template, ctx.template_context())
        return {"subject": subject, "body_html": body_html}

    def preview_report(self, case_id: int, selection: SendSelection,
                       user_id: Optional[int]):
        """Report preview: identical render path as the actual send."""
        ctx = self._case_context(case_id)
        self._require_customer(ctx)
        return self.reports.render(ctx, selection.report_template,
                                   selection.report_format, user_id)

    # -------------------------------------------------------------------- send

    def send(self, case_id: int, analyst_id: Optional[int],
             analyst_display: str, selection: SendSelection) -> SendResult:
        """Executes a complete send attempt (including the note)."""
        ctx: Optional[CaseContext] = None
        recipients: Optional[ResolvedRecipients] = None
        subject: Optional[str] = None
        body_html: Optional[str] = None
        artifact = None
        secrets = self.config.secrets()

        try:
            ctx = self._case_context(case_id)
            recipients = self._resolve(ctx, force_test_send=selection.test_send)
            subject = self._final_subject(ctx, selection,
                                          recipients.test_mode_active)
            body_html = self.templates.render_mail_body(
                selection.mail_template, ctx.template_context())
            artifact = self.reports.render(ctx, selection.report_template,
                                           selection.report_format, analyst_id)
            self.smtp.send(recipients, subject, body_html, artifact)
        except MailerError as exc:
            return self._handle_failure(ctx, analyst_id, analyst_display,
                                        recipients, subject, body_html,
                                        selection, artifact, exc.user_message,
                                        details=exc.details)
        except Exception as exc:  # noqa: BLE001 – defensive: nothing may leak through
            message = f"Unexpected error: {mask_secrets(str(exc), secrets)}"
            return self._handle_failure(ctx, analyst_id, analyst_display,
                                        recipients, subject, body_html,
                                        selection, artifact, message,
                                        details=repr(exc))

        # Success case: the note is mandatory, but its failure is no longer
        # a send failure – the mail is out. The analyst is informed.
        try:
            note_id, note_title = self.notes.create_send_note(
                case_ctx=ctx, analyst_id=analyst_id,
                analyst_display=analyst_display, recipients=recipients,
                subject=subject, body_html=body_html, selection=selection,
                artifact=artifact, success=True)
            return SendResult(
                success=True,
                message=f"Email successfully sent to: {', '.join(recipients.to)}.",
                note_id=note_id, note_title=note_title)
        except MailerError as exc:
            self.logger.error("Send ok, but note failed: %s"
                              % exc.user_message)
            return SendResult(
                success=True,
                message=("Email was sent, but the documentation note could "
                         f"not be created: {exc.user_message} "
                         "Please document manually."))

    def _handle_failure(self, ctx, analyst_id, analyst_display, recipients,
                        subject, body_html, selection, artifact,
                        user_message: str, details: Optional[str]) -> SendResult:
        secrets = self.config.secrets()
        masked = mask_secrets(user_message, secrets)
        self.logger.error("Send failed: %s | Details: %s"
                          % (masked, mask_secrets(details or "-", secrets)))

        note_id = None
        note_title = None
        note_info = ""
        if ctx is not None:
            try:
                note_id, note_title = self.notes.create_send_note(
                    case_ctx=ctx, analyst_id=analyst_id,
                    analyst_display=analyst_display, recipients=recipients,
                    subject=subject, body_html=body_html, selection=selection,
                    artifact=artifact, success=False, error_message=masked)
            except MailerError as note_exc:
                note_info = (" Additionally, no failure note could be "
                             f"created: {note_exc.user_message}")
                self.logger.error("Failure note failed: %s"
                                  % note_exc.user_message)
        else:
            note_info = " (No note possible – the case could not be loaded.)"

        return SendResult(success=False,
                          message=f"Send failed: {masked}{note_info}",
                          note_id=note_id, note_title=note_title)
