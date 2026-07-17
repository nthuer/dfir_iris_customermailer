"""Template service: renders subject and HTML mail body.

Uses the same template syntax as the IRIS report templates (Jinja2,
as used by docxtpl and the IRIS reporter): ``{{ ... }}``, ``{% ... %}``.

Security aspects:
- SandboxedEnvironment: no access to dangerous Python internals.
- StrictUndefined: missing variables cause a hard error (requirement:
  template errors block the send).
- Autoescape for the HTML body: case data (name, description) is
  escaped and cannot inject HTML/JS into the customer mail.
- Template names are checked against the directory listing (no path
  traversal via frontend input).
"""

from __future__ import annotations

import os
from typing import Dict, List

from jinja2 import FileSystemLoader, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .errors import TemplateRenderError
from .models import MailerConfig

MAIL_TEMPLATE_EXTENSIONS = (".html", ".htm")


class TemplateService:

    def __init__(self, config: MailerConfig):
        self._config = config
        self._body_env = SandboxedEnvironment(
            loader=FileSystemLoader(config.mail_templates_dir),
            autoescape=True,
            undefined=StrictUndefined,
        )
        self._subject_env = SandboxedEnvironment(
            autoescape=False,
            undefined=StrictUndefined,
        )

    def list_mail_templates(self) -> List[str]:
        """Available HTML mail templates (optionally filtered by allowlist)."""
        directory = self._config.mail_templates_dir
        if not os.path.isdir(directory):
            raise TemplateRenderError(
                f"Mail template directory not found: {directory}. "
                f"Please check 'mail_templates_dir' in the module configuration."
            )
        names = sorted(
            entry for entry in os.listdir(directory)
            if entry.lower().endswith(MAIL_TEMPLATE_EXTENSIONS)
            and os.path.isfile(os.path.join(directory, entry))
        )
        allowlist = self._config.allowed_mail_templates
        if allowlist:
            names = [n for n in names if n in allowlist]
        return names

    def _check_template_name(self, template_name: str) -> None:
        # No path traversal: only exact names from the directory listing.
        if (not template_name
                or any(sep in template_name for sep in ("/", "\\", ".."))
                or template_name not in self.list_mail_templates()):
            raise TemplateRenderError(
                f"Mail template '{template_name}' is not available or not allowed."
            )

    def render_mail_body(self, template_name: str, context: Dict) -> str:
        """Renders the HTML mail body. Errors block the send hard."""
        self._check_template_name(template_name)
        try:
            return self._body_env.get_template(template_name).render(**context)
        except TemplateError as exc:
            raise TemplateRenderError(
                f"Mail template '{template_name}' cannot be rendered: {exc}. "
                f"Send blocked.",
                details=repr(exc),
            )

    def render_subject(self, subject_template: str, context: Dict) -> str:
        """Renders the subject from a template string (single line, plain text)."""
        try:
            subject = self._subject_env.from_string(subject_template).render(**context)
        except TemplateError as exc:
            raise TemplateRenderError(
                f"Subject template cannot be rendered: {exc}. Send blocked.",
                details=repr(exc),
            )
        # Prevent header injection: remove line breaks.
        return " ".join(subject.split())
