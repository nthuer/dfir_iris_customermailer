"""Tests: mail templates written in the IRIS module configuration editor.

IRIS renders a parameter of type ``textfield_html`` as a code editor, so
templates can be maintained in the web interface without mounting a
directory. One parameter holds several named templates.
"""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.errors import TemplateRenderError
from iris_customer_case_mailer_module.customer_case_mailer.models import (
    CaseContext,
    CustomerContact,
)
from iris_customer_case_mailer_module.customer_case_mailer.template_service import (
    UNNAMED_TEMPLATE,
    TemplateService,
    parse_inline_templates,
)

TWO_TEMPLATES = """
Anything before the first marker is ignored.

<!-- template: closing_report -->
<html><body><h1>Closing: {{ case.name }}</h1></body></html>

<!-- template: interim_update -->
<html><body><p>Interim for {{ case.for_customer }}</p></body></html>
"""


@pytest.fixture
def context():
    return CaseContext(
        case_id=1, name="Ransomware", description="d", open_date="2026-07-01",
        soc_id="SOC-1", customer_name="ACME Corp",
        contacts=[CustomerContact("Jane", "ciso@example.org", "CISO")],
    ).template_context()


def service_for(raw_config, blob, **overrides):
    raw_config["mail_templates_html"] = blob
    raw_config.update(overrides)
    return TemplateService(load_config(raw_config))


# ------------------------------------------------------------- parsing

def test_parses_several_named_templates():
    parsed = parse_inline_templates(TWO_TEMPLATES)
    assert list(parsed) == ["closing_report", "interim_update"]
    assert parsed["closing_report"].startswith("<html>")
    assert "Interim" in parsed["interim_update"]
    assert "ignored" not in parsed["closing_report"]


def test_text_without_marker_is_one_default_template():
    parsed = parse_inline_templates("<html><body>Hi</body></html>")
    assert parsed == {UNNAMED_TEMPLATE: "<html><body>Hi</body></html>"}


def test_empty_editor_yields_no_templates():
    assert parse_inline_templates("") == {}
    assert parse_inline_templates("   \n  ") == {}


def test_marker_is_case_insensitive_and_tolerates_spacing():
    parsed = parse_inline_templates("<!--TEMPLATE :  My Template  -->\n<p>x</p>")
    assert list(parsed) == ["My Template"]


def test_duplicate_names_are_rejected():
    with pytest.raises(TemplateRenderError, match="more than once"):
        parse_inline_templates("<!-- template: a -->\n<p>1</p>\n<!-- template: a -->\n<p>2</p>")


def test_nameless_marker_is_rejected():
    with pytest.raises(TemplateRenderError, match="no name"):
        parse_inline_templates("<!-- template: -->\n<p>x</p>")


def test_empty_template_is_rejected():
    with pytest.raises(TemplateRenderError, match="is empty"):
        parse_inline_templates("<!-- template: a -->\n\n<!-- template: b -->\n<p>x</p>")


# ------------------------------------------------- listing and rendering

def test_editor_templates_are_listed_next_to_files(raw_config):
    service = service_for(raw_config, TWO_TEMPLATES)
    assert service.list_mail_templates() == [
        "broken.html", "closing_report", "interim_update", "standard.html"]


def test_editor_template_renders_case_variables(raw_config, context):
    service = service_for(raw_config, TWO_TEMPLATES)
    html = service.render_mail_body("closing_report", context)
    assert "Closing: Ransomware" in html
    assert "ACME Corp" in service.render_mail_body("interim_update", context)


def test_editor_template_wins_over_a_file_of_the_same_name(raw_config, context):
    service = service_for(raw_config,
                          "<!-- template: standard.html -->\n<p>from the editor</p>")
    assert "from the editor" in service.render_mail_body("standard.html", context)


def test_editor_templates_are_sandboxed_and_strict(raw_config, context):
    service = service_for(raw_config, "<!-- template: broken -->\n{{ case.nope }}")
    with pytest.raises(TemplateRenderError, match="cannot be rendered"):
        service.render_mail_body("broken", context)


def test_editor_template_escapes_case_data(raw_config):
    service = service_for(raw_config, "<!-- template: t -->\n<p>{{ case.name }}</p>")
    ctx = CaseContext(case_id=1, name="<script>x</script>", description="d",
                      open_date="2026-07-01", soc_id="S", customer_name="ACME").template_context()
    assert "&lt;script&gt;" in service.render_mail_body("t", ctx)


def test_allowlist_also_applies_to_editor_templates(raw_config):
    service = service_for(raw_config, TWO_TEMPLATES,
                          allowed_mail_templates="closing_report")
    assert service.list_mail_templates() == ["closing_report"]


def test_editor_alone_is_enough_without_a_template_directory(raw_config, context):
    """No mounted and no bundled directory - the editor carries everything."""
    raw_config["mail_templates_dir"] = "/does/not/exist"
    service = service_for(raw_config, TWO_TEMPLATES)
    assert service.list_mail_templates() == ["closing_report", "interim_update"]
    assert "Closing" in service.render_mail_body("closing_report", context)


def test_missing_directory_and_empty_editor_is_reported(raw_config):
    raw_config["mail_templates_dir"] = "/does/not/exist"
    service = service_for(raw_config, "")
    with pytest.raises(TemplateRenderError, match="No mail templates available"):
        service.list_mail_templates()
