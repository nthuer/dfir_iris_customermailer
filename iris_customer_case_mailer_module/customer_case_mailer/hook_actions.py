"""Manual case hooks offered by the module and what each one does.

Kept free of IRIS imports so the dispatch can be unit-tested; the IRIS
interface only resolves the analyst and delegates here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional

from .audit_service import mask_secrets
from .models import SendResult
from .notes_service import (
    KIND_FAILED,
    KIND_PREVIEW_FAILED,
    build_note_content,
    build_note_title,
)

HOOK_PREVIEW = "Preview customer report"
HOOK_SEND = "Send customer report"
HOOK_TEST_SEND = "Test send customer report"

# Order = order of registration = order shown in the IRIS case menu.
MANUAL_HOOKS = (HOOK_PREVIEW, HOOK_SEND, HOOK_TEST_SEND)

DEFAULT_NOTES_DIRECTORY = "Communication"


def run_hook(mailer, hook_ui_name: str, case_id: int,
             analyst_id: Optional[int], analyst_display: str) -> SendResult:
    """Runs the action behind a manual hook for one case."""
    if hook_ui_name == HOOK_PREVIEW:
        return mailer.preview(case_id, analyst_id, analyst_display)
    if hook_ui_name == HOOK_SEND:
        return mailer.send(case_id, analyst_id, analyst_display)
    if hook_ui_name == HOOK_TEST_SEND:
        return mailer.send(case_id, analyst_id, analyst_display, test_send=True)
    raise ValueError(f"Unknown hook '{hook_ui_name}'")


def report_setup_failure(adapter, raw_config: Dict, hook_ui_name: str,
                         case_id: int, analyst_id: Optional[int],
                         analyst_display: str, message: str) -> Optional[int]:
    """Writes a failure note when the module cannot even start.

    Invalid module configuration is detected before any case is touched.
    Without this note the analyst would see nothing but IRIS' "Queued
    task". Best effort: returns the note id, or None if even this fails.
    """
    kind = KIND_PREVIEW_FAILED if hook_ui_name == HOOK_PREVIEW else KIND_FAILED
    secrets = [raw_config.get("smtp_password")] if raw_config.get("smtp_password") else []
    directory = (str(raw_config.get("notes_directory_name") or "").strip()
                 or DEFAULT_NOTES_DIRECTORY)
    timestamp = datetime.now(timezone.utc)

    try:
        customer = adapter.get_case_context(case_id).customer_name
    except Exception:  # noqa: BLE001
        customer = None

    error = (f"{mask_secrets(message, secrets)}\n\n"
             "The module configuration is incomplete or invalid. An IRIS "
             "administrator has to fix it in Advanced -> Modules -> "
             "IrisCustomerCaseMailer.")
    content = build_note_content(
        kind=kind, timestamp=timestamp, analyst_display=analyst_display,
        recipients=None, subject=None, body_html=None, selection=None,
        artifact=None, attachment_line="no attachment", error_message=error)
    try:
        directory_id = adapter.ensure_note_directory(case_id, directory, analyst_id)
        return adapter.add_note(case_id=case_id, directory_id=directory_id,
                                title=build_note_title(timestamp, customer, kind),
                                content=content, user_id=analyst_id)
    except Exception:  # noqa: BLE001
        return None
