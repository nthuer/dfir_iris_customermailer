"""Notes service: documents every preview and every send attempt as a note.

The note is the analyst's feedback channel: IRIS only answers a manual
hook with "Queued task", so results and errors become visible in the
case as notes.

Rules:
- Every send attempt (success or failure) creates exactly one note.
  Every preview creates exactly one note as well.
- Stored in the note directory ``Communication`` (configurable); the
  directory is created automatically when missing.
- The rendered report is stored as a real file in the IRIS datastore
  and linked in the note (IRIS notes have no native attachment field).
- Titles (IRIS limits note titles to 155 characters):
    ``Customer mail YYYY-MM-DD HH:MM – <Customer>``                  sent
    ``FAILED – Customer mail YYYY-MM-DD HH:MM – <Customer>``         send failed
    ``PREVIEW – Customer mail YYYY-MM-DD HH:MM – <Customer>``        preview
    ``PREVIEW FAILED – Customer mail YYYY-MM-DD HH:MM – <Customer>`` preview failed
- No separate status field: the state is recognisable via title and
  content only.
- Secrets are masked before writing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from .audit_service import mask_secrets
from .errors import MailerError, NotesError
from .models import (
    CaseContext,
    MailerConfig,
    ReportArtifact,
    ResolvedRecipients,
    SendSelection,
)

KIND_SENT = "sent"
KIND_FAILED = "failed"
KIND_PREVIEW = "preview"
KIND_PREVIEW_FAILED = "preview_failed"

_TITLE_PREFIX = {
    KIND_SENT: "",
    KIND_FAILED: "FAILED – ",
    KIND_PREVIEW: "PREVIEW – ",
    KIND_PREVIEW_FAILED: "PREVIEW FAILED – ",
}

_HEADLINE = {
    KIND_SENT: "Customer mail – sent",
    KIND_FAILED: "Customer mail – FAILED",
    KIND_PREVIEW: "Customer mail – PREVIEW (nothing has been sent)",
    KIND_PREVIEW_FAILED: "Customer mail – PREVIEW FAILED (nothing has been sent)",
}

IRIS_NOTE_TITLE_MAX = 155


def fingerprint_marker(fingerprint: str) -> str:
    """Line written into preview notes; searched for by the preview gate."""
    return f"Preview fingerprint: `{fingerprint}`"


def build_note_title(timestamp: datetime, customer_name: Optional[str],
                     kind: str) -> str:
    stamp = timestamp.strftime("%Y-%m-%d %H:%M")
    head = f"{_TITLE_PREFIX[kind]}Customer mail {stamp} – "
    customer = customer_name or "Unknown customer"
    room = IRIS_NOTE_TITLE_MAX - len(head)
    if len(customer) > room:
        customer = customer[:room - 1] + "…"
    return head + customer


def preview_title_prefix() -> str:
    return _TITLE_PREFIX[KIND_PREVIEW]


def _fmt_list(values: List[str]) -> str:
    return ", ".join(values) if values else "–"


def build_note_content(kind: str,
                       timestamp: datetime,
                       analyst_display: str,
                       recipients: Optional[ResolvedRecipients],
                       subject: Optional[str],
                       body_html: Optional[str],
                       selection: Optional[SendSelection],
                       artifact: Optional[ReportArtifact],
                       attachment_line: str,
                       fingerprint: Optional[str] = None,
                       error_message: Optional[str] = None) -> str:
    """Builds the markdown content of the note (pure function, testable)."""
    lines = [
        f"# {_HEADLINE[kind]}",
        "",
        f"- **Timestamp:** {timestamp.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- **Analyst:** {analyst_display}",
    ]
    if recipients is not None:
        lines += [
            f"- **To:** {_fmt_list(recipients.to)}",
            f"- **CC:** {_fmt_list(recipients.cc)}",
            f"- **BCC:** {_fmt_list(recipients.bcc)}",
        ]
        if recipients.skipped_invalid:
            lines.append(
                "- **Skipped contacts (invalid email address):** "
                f"{_fmt_list(recipients.skipped_invalid)}")
        if recipients.test_mode_active:
            lines += [
                "- **Test mode:** active – production recipients were replaced",
                f"- **Production To (not contacted):** {_fmt_list(recipients.original_to)}",
                f"- **Production CC (not contacted):** {_fmt_list(recipients.original_cc)}",
                f"- **Production BCC (not contacted):** {_fmt_list(recipients.original_bcc)}",
            ]
    else:
        lines.append("- **Recipients:** could not be determined")

    lines.append(f"- **Subject:** {subject if subject else '–'}")
    if selection is not None:
        # The rendered artifact knows the template's display name; before
        # rendering only the configured reference (name or id) is known.
        report_template = artifact.template_name if artifact else selection.report_template
        lines += [
            f"- **Mail template:** {selection.mail_template or '–'}",
            f"- **Report template:** {report_template or '–'} "
            f"({selection.report_format or '–'})",
        ]
    if artifact is not None:
        lines.append(f"- **Report file:** {attachment_line}")
    elif kind in (KIND_FAILED, KIND_PREVIEW_FAILED):
        lines.append("- **Report file:** not generated (error before rendering)")
    if fingerprint and kind == KIND_PREVIEW:
        lines.append(f"- {fingerprint_marker(fingerprint)}")
    elif fingerprint:
        # Deliberately worded differently: the preview gate only accepts
        # the marker above, and only in PREVIEW notes.
        lines.append(f"- **Content fingerprint:** `{fingerprint}` "
                     "(the preview note with the same fingerprint shows this content)")

    if kind == KIND_PREVIEW:
        lines += ["", "_Review recipients, subject, mail and report. If everything is "
                      "correct, run **Send customer report** on this case. Any change to "
                      "the case data or the send options requires a new preview._"]

    if error_message:
        lines += ["", "## Error", "", error_message]

    if body_html:
        lines += ["", "## Mail body (HTML)", "", "```html", body_html, "```"]
    else:
        lines += ["", "## Mail body", "", "_Mail body was not rendered._"]

    return "\n".join(lines)


class NotesService:

    def __init__(self, config: MailerConfig, adapter, logger):
        self._config = config
        self._adapter = adapter
        self._logger = logger

    def preview_exists(self, case_id: int, fingerprint: str) -> bool:
        """True if a preview note with exactly this content exists."""
        return self._adapter.note_exists(
            case_id=case_id,
            title_prefix=preview_title_prefix(),
            content_fragment=fingerprint_marker(fingerprint),
        )

    def create_note(self,
                    kind: str,
                    case_ctx: CaseContext,
                    analyst_id: Optional[int],
                    analyst_display: str,
                    recipients: Optional[ResolvedRecipients],
                    subject: Optional[str],
                    body_html: Optional[str],
                    selection: Optional[SendSelection],
                    artifact: Optional[ReportArtifact],
                    fingerprint: Optional[str] = None,
                    error_message: Optional[str] = None,
                    timestamp: Optional[datetime] = None,
                    ) -> Tuple[int, str]:
        """Creates exactly one note for this preview or send attempt.

        Raises :class:`NotesError` when the directory or the note cannot
        be created. A failed file attachment does NOT abort the note –
        the mail may already have been sent at this point – but is
        documented inside the note.
        """
        timestamp = timestamp or datetime.now(timezone.utc)
        secrets = self._config.secrets()

        try:
            directory_id = self._adapter.ensure_note_directory(
                case_id=case_ctx.case_id,
                name=self._config.notes_directory_name,
                user_id=analyst_id,
            )
        except MailerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise NotesError(
                f"Note directory '{self._config.notes_directory_name}' could "
                f"not be created: {mask_secrets(str(exc), secrets)}",
                details=repr(exc),
            )

        attachment_line = "no attachment"
        if artifact is not None:
            try:
                _file_id, link = self._adapter.store_report_in_datastore(
                    case_id=case_ctx.case_id,
                    filename=artifact.filename,
                    content=artifact.content,
                    user_id=analyst_id,
                )
                attachment_line = f"[{artifact.filename}]({link})"
            except Exception as exc:  # noqa: BLE001
                masked = mask_secrets(str(exc), secrets)
                self._logger.error(
                    "Report could not be attached to the note: %s" % masked)
                attachment_line = (
                    f"COULD NOT BE ATTACHED ({artifact.filename}): {masked}"
                )

        content = build_note_content(
            kind=kind,
            timestamp=timestamp,
            analyst_display=analyst_display,
            recipients=recipients,
            subject=subject,
            body_html=body_html,
            selection=selection,
            artifact=artifact,
            attachment_line=attachment_line,
            fingerprint=fingerprint,
            error_message=mask_secrets(error_message, secrets) if error_message else None,
        )
        title = build_note_title(timestamp, case_ctx.customer_name, kind)

        try:
            note_id = self._adapter.add_note(
                case_id=case_ctx.case_id,
                directory_id=directory_id,
                title=title,
                content=content,
                user_id=analyst_id,
            )
        except MailerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise NotesError(
                f"Note could not be saved: "
                f"{mask_secrets(str(exc), secrets)}",
                details=repr(exc),
            )

        return note_id, title
