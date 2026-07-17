"""Notes service: documents every send attempt as a case note.

Rules:
- After EVERY send attempt (success or failure) exactly one note is
  created.
- Stored in the note directory ``Communication`` (configurable); the
  directory is created automatically when missing.
- The actually sent report is stored as a real file (IRIS datastore)
  and linked in the note. IRIS notes have no native attachment field;
  the datastore is the IRIS-conformant location for files on a case –
  the note references the datastore entry with the usual DSF link.
- No separate status field: success/failure is only recognisable via
  the title prefix ``FAILED – `` and the content.
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


def build_note_title(timestamp: datetime, customer_name: Optional[str],
                     success: bool) -> str:
    stamp = timestamp.strftime("%Y-%m-%d %H:%M")
    customer = customer_name or "Unknown customer"
    title = f"Customer mail {stamp} – {customer}"
    return title if success else f"FAILED – {title}"


def _fmt_list(values: List[str]) -> str:
    return ", ".join(values) if values else "–"


def build_note_content(timestamp: datetime,
                       analyst_display: str,
                       recipients: Optional[ResolvedRecipients],
                       subject: Optional[str],
                       body_html: Optional[str],
                       selection: Optional[SendSelection],
                       artifact: Optional[ReportArtifact],
                       attachment_line: str,
                       success: bool,
                       error_message: Optional[str] = None) -> str:
    """Builds the markdown content of the note (pure function, testable)."""
    lines = [
        f"# Customer mail – {'sent' if success else 'FAILED'}",
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
        lines += [
            f"- **Mail template:** {selection.mail_template}",
            f"- **Report template:** {selection.report_template} ({selection.report_format})",
        ]
    if artifact is not None:
        lines.append(f"- **Report file:** {attachment_line}")
    elif not success:
        lines.append("- **Report file:** not generated (error before rendering)")

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

    def create_send_note(self,
                         case_ctx: CaseContext,
                         analyst_id: Optional[int],
                         analyst_display: str,
                         recipients: Optional[ResolvedRecipients],
                         subject: Optional[str],
                         body_html: Optional[str],
                         selection: Optional[SendSelection],
                         artifact: Optional[ReportArtifact],
                         success: bool,
                         error_message: Optional[str] = None,
                         timestamp: Optional[datetime] = None,
                         ) -> Tuple[int, str]:
        """Creates exactly one note for this send attempt.

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
            timestamp=timestamp,
            analyst_display=analyst_display,
            recipients=recipients,
            subject=subject,
            body_html=body_html,
            selection=selection,
            artifact=artifact,
            attachment_line=attachment_line,
            success=success,
            error_message=mask_secrets(error_message, secrets) if error_message else None,
        )
        title = build_note_title(timestamp, case_ctx.customer_name, success)

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
