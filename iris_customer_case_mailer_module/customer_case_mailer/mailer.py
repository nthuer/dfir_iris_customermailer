"""Orchestrator: wires all services into the preview and send flows.

Both flows render the same content in the same order:

    1. Load/validate config              (config_service)
    2. Load case + customer + contacts   (iris_adapter)
       and the per-case send options (case custom attributes)
    3. Resolve/validate recipients       (recipient_service)
       (contacts with role CISO that carry a valid email address)
    4. Render subject + mail body        (template_service)   -> hard errors
    5. Render investigation report       (report_service)     -> hard errors
    6. Fingerprint the result            (recipients, subject, body, choices)

Preview then stores a PREVIEW note (nothing is sent). Send additionally
checks that a preview note with the same fingerprint exists (unless
disabled or a test send), sends via SMTP and stores the send note.

IRIS answers a manual hook only with "Queued task", so every outcome –
including every failure – is written into the case as a note.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional

from .audit_service import get_logger, mask_secrets
from .config_service import load_config
from .errors import ConfigError, MailerError, PreviewRequiredError, RecipientError
from .iris_adapter import IrisAdapter
from .models import CaseContext, RenderedMail, ResolvedRecipients, SendResult, SendSelection
from .notes_service import (
    KIND_FAILED,
    KIND_PREVIEW,
    KIND_PREVIEW_FAILED,
    KIND_SENT,
    NotesService,
)
from .recipient_service import resolve_recipients
from .report_service import ReportService
from .smtp_service import SmtpService
from .template_service import TemplateService

TEST_SUBJECT_PREFIX = "[TEST MODE] "


def fingerprint(recipients: ResolvedRecipients, subject: str, body_html: str,
                selection: SendSelection) -> str:
    """Stable hash of everything the customer would receive.

    The report bytes are deliberately excluded (DOCX files embed
    timestamps); the report template and format are included instead.
    """
    payload = json.dumps({
        "to": recipients.to,
        "cc": recipients.cc,
        "bcc": recipients.bcc,
        "test_mode": recipients.test_mode_active,
        "subject": subject,
        "body": body_html,
        "mail_template": selection.mail_template,
        "report_template": selection.report_template,
        "report_format": selection.report_format,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


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

    # ------------------------------------------------------------ rendering

    def resolve_selection(self, ctx: CaseContext, test_send: bool = False) -> SendSelection:
        """Per-case choices (case custom attributes), else module defaults."""
        opts = ctx.send_options or {}
        mail_template = opts.get("mail_template") or self.config.default_mail_template
        report_template = opts.get("report_template") or self.config.default_report_template
        report_format = (opts.get("report_format") or self.config.default_report_format).lower()

        if not mail_template:
            raise ConfigError(
                "No mail template selected. Set the case attribute 'Mail template' "
                "or the module parameter 'default_mail_template'.")
        if not report_template:
            raise ConfigError(
                "No investigation report template selected. Set the case attribute "
                "'Report template' or the module parameter 'default_report_template'.")

        return SendSelection(
            mail_template=mail_template,
            report_template=report_template,
            report_format=report_format,
            subject_override=opts.get("subject") or None,
            test_send=test_send,
        )

    def _subject(self, ctx: CaseContext, selection: SendSelection,
                 test_mode_active: bool) -> str:
        # A subject set on the case is used as final text (no re-render,
        # so typed { } characters stay untouched).
        if selection.subject_override and selection.subject_override.strip():
            subject = " ".join(selection.subject_override.split())
        else:
            subject = self.templates.render_subject(
                self.config.default_subject_template, ctx.template_context())
        if test_mode_active:
            subject = TEST_SUBJECT_PREFIX + subject
        return subject

    def _render(self, case_id: int, analyst_id: Optional[int], test_send: bool,
                partial: Dict) -> RenderedMail:
        """Renders everything a send would transmit.

        ``partial`` is filled step by step, so a failure note can still
        document everything that was resolved before the error.
        """
        ctx = self.adapter.get_case_context(case_id)
        partial["ctx"] = ctx
        if not ctx.customer_name:
            raise RecipientError("No customer is assigned to this case.")

        selection = self.resolve_selection(ctx, test_send)
        partial["selection"] = selection

        recipients = resolve_recipients(ctx.contacts, self.config,
                                        force_test_send=test_send)
        partial["recipients"] = recipients

        subject = self._subject(ctx, selection, recipients.test_mode_active)
        partial["subject"] = subject

        body_html = self.templates.render_mail_body(
            selection.mail_template, ctx.template_context())
        partial["body_html"] = body_html

        artifact = self.reports.render(ctx, selection.report_template,
                                       selection.report_format, analyst_id)
        partial["artifact"] = artifact

        return RenderedMail(
            recipients=recipients, subject=subject, body_html=body_html,
            artifact=artifact, selection=selection,
            fingerprint=fingerprint(recipients, subject, body_html, selection))

    # --------------------------------------------------------------- flows

    def preview(self, case_id: int, analyst_id: Optional[int],
                analyst_display: str) -> SendResult:
        """Renders everything and stores it as a PREVIEW note. Sends nothing."""
        partial: Dict = {}
        try:
            rendered = self._render(case_id, analyst_id, test_send=False, partial=partial)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a note
            return self._failure(KIND_PREVIEW_FAILED, partial, analyst_id,
                                 analyst_display, exc, "Preview failed")

        try:
            note_id, title = self.notes.create_note(
                KIND_PREVIEW, partial["ctx"], analyst_id, analyst_display,
                rendered.recipients, rendered.subject, rendered.body_html,
                rendered.selection, rendered.artifact,
                fingerprint=rendered.fingerprint)
        except MailerError as exc:
            self.logger.error("Preview note failed: %s" % exc.user_message)
            return SendResult(False, f"Preview could not be stored: {exc.user_message}")
        return SendResult(True, f"Preview stored as note '{title}'.",
                          note_id=note_id, note_title=title)

    def send(self, case_id: int, analyst_id: Optional[int],
             analyst_display: str, test_send: bool = False) -> SendResult:
        """Executes one complete send attempt, always followed by one note."""
        partial: Dict = {}
        try:
            rendered = self._render(case_id, analyst_id, test_send=test_send,
                                    partial=partial)
            partial["fingerprint"] = rendered.fingerprint

            gate = (self.config.require_preview_before_send
                    and not rendered.recipients.test_mode_active)
            if gate and not self.notes.preview_exists(case_id, rendered.fingerprint):
                raise PreviewRequiredError(
                    "No matching preview found. Run 'Preview customer report' on "
                    "this case and check the preview note first. If a preview "
                    "exists, the case data or send options changed since then.")

            self.smtp.send(rendered.recipients, rendered.subject,
                           rendered.body_html, rendered.artifact)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a note
            return self._failure(KIND_FAILED, partial, analyst_id,
                                 analyst_display, exc, "Send failed")

        # The mail is out. A failing note is reported, but no longer a
        # send failure.
        try:
            note_id, title = self.notes.create_note(
                KIND_SENT, partial["ctx"], analyst_id, analyst_display,
                rendered.recipients, rendered.subject, rendered.body_html,
                rendered.selection, rendered.artifact,
                fingerprint=rendered.fingerprint)
        except MailerError as exc:
            self.logger.error("Send ok, but note failed: %s" % exc.user_message)
            return SendResult(True, (
                "Email was sent, but the documentation note could not be "
                f"created: {exc.user_message} Please document manually."))
        return SendResult(
            True, f"Email sent to: {', '.join(rendered.recipients.to)}.",
            note_id=note_id, note_title=title)

    # -------------------------------------------------------------- errors

    def _failure(self, kind: str, partial: Dict, analyst_id: Optional[int],
                 analyst_display: str, exc: Exception, label: str) -> SendResult:
        secrets = self.config.secrets()
        if isinstance(exc, MailerError):
            message, details = exc.user_message, exc.details
        else:
            message = f"Unexpected error: {exc}"
            details = repr(exc)
        masked = mask_secrets(message, secrets)
        self.logger.error("%s: %s | Details: %s"
                          % (label, masked, mask_secrets(details or "-", secrets)))

        ctx = partial.get("ctx")
        if ctx is None:
            return SendResult(False, f"{label}: {masked} (No note possible – "
                                     f"the case could not be loaded.)")
        try:
            note_id, title = self.notes.create_note(
                kind, ctx, analyst_id, analyst_display,
                partial.get("recipients"), partial.get("subject"),
                partial.get("body_html"), partial.get("selection"),
                partial.get("artifact"), fingerprint=partial.get("fingerprint"),
                error_message=masked)
        except MailerError as note_exc:
            self.logger.error("Failure note failed: %s" % note_exc.user_message)
            return SendResult(False, f"{label}: {masked} Additionally, no note "
                                     f"could be created: {note_exc.user_message}")
        return SendResult(False, f"{label}: {masked}", note_id=note_id, note_title=title)
