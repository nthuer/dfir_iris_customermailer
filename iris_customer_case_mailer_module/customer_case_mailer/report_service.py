"""Report service: renders investigation reports as DOCX or HTML.

Only the investigation report templates maintained in IRIS are used
(report type "Investigation"). The actual rendering is done by the
IRIS reporter (docxtpl for DOCX, Jinja2 for HTML) – encapsulated in
:mod:`iris_adapter`.

The format of a template is derived from its template file
(.docx -> docx, .html/.htm/.md -> html). The analyst may only pick a
format that (a) is enabled in ``allowed_report_formats`` and (b)
matches the selected template.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .errors import MailerError, ReportRenderError
from .models import CaseContext, MailerConfig, ReportArtifact

MIMETYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "html": "text/html",
}

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_filename_part(value: str) -> str:
    return _SAFE_CHARS.sub("_", value).strip("_") or "case"


class ReportService:

    def __init__(self, config: MailerConfig, adapter):
        self._config = config
        self._adapter = adapter

    def list_templates(self) -> List[Dict]:
        """Selectable investigation templates.

        Returns ``[{"id", "name", "description", "format"}]`` – filtered
        by allowed formats and (optionally) the template allowlist.
        """
        templates = self._adapter.list_investigation_report_templates()
        allowlist = self._config.allowed_report_templates
        result = []
        for tpl in templates:
            if tpl.get("format") not in self._config.allowed_report_formats:
                continue
            if allowlist and tpl["name"] not in allowlist and str(tpl["id"]) not in allowlist:
                continue
            result.append(tpl)
        return result

    def _find_template(self, template_ref: str) -> Dict:
        """Finds a template by id or name within the allowed list."""
        ref = str(template_ref).strip()
        for tpl in self.list_templates():
            if ref in (str(tpl["id"]), tpl["name"]):
                return tpl
        raise ReportRenderError(
            f"Investigation report template '{template_ref}' is not "
            f"available or not allowed."
        )

    def render(self, case_ctx: CaseContext, template_ref: str,
               report_format: str, user_id: Optional[int]) -> ReportArtifact:
        """Renders the report and returns it as an artifact (bytes)."""
        fmt = (report_format or "").strip().lower()
        if fmt not in ("docx", "html"):
            raise ReportRenderError(f"Unknown report format: '{report_format}'.")
        if fmt not in self._config.allowed_report_formats:
            raise ReportRenderError(f"Report format '{fmt}' is not enabled.")

        template = self._find_template(template_ref)
        if template.get("format") != fmt:
            raise ReportRenderError(
                f"Template '{template['name']}' is stored as "
                f"{template.get('format', 'unknown')} and cannot be rendered as "
                f"{fmt}. Please pick a matching template."
            )

        try:
            content = self._adapter.generate_report(
                case_id=case_ctx.case_id,
                template_id=template["id"],
                report_format=fmt,
                user_id=user_id,
            )
        except MailerError:
            raise
        except Exception as exc:  # noqa: BLE001 – map reporter errors hard
            raise ReportRenderError(
                f"Investigation report could not be rendered "
                f"(template '{template['name']}'): {exc}. Send blocked.",
                details=repr(exc),
            )

        if not content:
            raise ReportRenderError(
                f"Investigation report (template '{template['name']}') was "
                f"rendered empty. Send blocked."
            )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        case_ref = _sanitize_filename_part(case_ctx.soc_id or str(case_ctx.case_id))
        filename = f"investigation_report_{case_ref}_{timestamp}.{fmt}"
        return ReportArtifact(
            filename=filename,
            content=content,
            mimetype=MIMETYPES[fmt],
            report_format=fmt,
            template_name=template["name"],
        )
