"""Template service: renders subject and HTML mail body.

Uses the same template syntax as the IRIS report templates (Jinja2,
as used by docxtpl and the IRIS reporter): ``{{ ... }}``, ``{% ... %}``.

Templates come from two sources:

1. The module configuration parameter ``mail_templates_html``. IRIS
   renders parameters of type ``textfield_html`` with a code editor, so
   templates can be written and changed directly in the IRIS web
   interface - no mounted directory required. One parameter holds any
   number of named templates, separated by marker comments::

       <!-- template: closing_report -->
       <html>…</html>
       <!-- template: interim_update -->
       <html>…</html>

2. HTML files in ``mail_templates_dir`` (the templates shipped with the
   module, or a mounted directory).

Both sources are offered together; on a name collision the template
from the editor wins.

Security aspects:
- SandboxedEnvironment: no access to dangerous Python internals.
- StrictUndefined: missing variables cause a hard error (requirement:
  template errors block the send).
- Autoescape for the HTML body: case data (name, description) is
  escaped and cannot inject HTML/JS into the customer mail.
- Template names are checked against the listing (no path traversal).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List

from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader, StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from .errors import TemplateRenderError
from .models import MailerConfig

MAIL_TEMPLATE_EXTENSIONS = (".html", ".htm")

# <!-- template: name --> on a line of its own.
TEMPLATE_MARKER = re.compile(
    r"^[ \t]*<!--[ \t]*template[ \t]*:[ \t]*(?P<name>[^>]*?)[ \t]*-->[ \t]*$",
    re.MULTILINE | re.IGNORECASE)

# Name used when the editor holds one template without any marker.
UNNAMED_TEMPLATE = "default"


def parse_inline_templates(blob: str) -> Dict[str, str]:
    """Splits the editor content into ``{name: html}``.

    Content before the first marker is ignored (room for comments). A
    non-empty text without any marker counts as a single template named
    ``default``, so a pasted template works without knowing the syntax.
    """
    if not blob or not blob.strip():
        return {}

    matches = list(TEMPLATE_MARKER.finditer(blob))
    if not matches:
        return {UNNAMED_TEMPLATE: blob}

    templates: Dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group("name").strip()
        if not name:
            raise TemplateRenderError(
                "A template marker in 'mail_templates_html' has no name. "
                "Expected: <!-- template: some_name -->")
        if name in templates:
            raise TemplateRenderError(
                f"The template name '{name}' is used more than once in "
                f"'mail_templates_html'. Names must be unique.")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        body = blob[match.end():end].strip()
        if not body:
            raise TemplateRenderError(
                f"The template '{name}' in 'mail_templates_html' is empty.")
        templates[name] = body
    return templates


class TemplateService:

    def __init__(self, config: MailerConfig):
        self._config = config
        self._inline = parse_inline_templates(config.mail_templates_html)
        self._body_env = SandboxedEnvironment(
            # The editor wins over files of the same name.
            loader=ChoiceLoader([DictLoader(self._inline),
                                 FileSystemLoader(config.mail_templates_dir)]),
            autoescape=True,
            undefined=StrictUndefined,
        )
        self._subject_env = SandboxedEnvironment(
            autoescape=False,
            undefined=StrictUndefined,
        )

    def list_mail_templates(self) -> List[str]:
        """Available mail templates from both sources (allowlist applied)."""
        names = set(self._inline)

        directory = self._config.mail_templates_dir
        if os.path.isdir(directory):
            names.update(
                entry for entry in os.listdir(directory)
                if entry.lower().endswith(MAIL_TEMPLATE_EXTENSIONS)
                and os.path.isfile(os.path.join(directory, entry)))
        elif not names:
            raise TemplateRenderError(
                f"No mail templates available: the directory '{directory}' does "
                f"not exist and 'mail_templates_html' is empty.")

        allowlist = self._config.allowed_mail_templates
        if allowlist:
            names = {n for n in names if n in allowlist}
        return sorted(names)

    def _check_template_name(self, template_name: str) -> None:
        # No path traversal: only exact names from the listing.
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
