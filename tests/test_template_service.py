"""Tests: mail/subject templates (rendering, hard errors, escaping)."""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.errors import TemplateRenderError
from iris_customer_case_mailer_module.customer_case_mailer.models import (
    CaseContext,
    CustomerContact,
)
from iris_customer_case_mailer_module.customer_case_mailer.template_service import (
    TemplateService,
)


@pytest.fixture
def service(raw_config):
    return TemplateService(load_config(raw_config))


@pytest.fixture
def context():
    return CaseContext(
        case_id=1, name="Case <Alpha>", description="Description",
        open_date="2026-07-01", soc_id="SOC-1",
        customer_name="ACME Corp",
        contacts=[CustomerContact("Jane Doe", "ciso@example.org", "CISO")],
    ).template_context()


def test_render_mail_body_with_case_variables(service, context):
    html = service.render_mail_body("standard.html", context)
    assert "Case" in html
    assert "ACME Corp" in html          # case.for_customer -> customer name
    assert "SOC-1" in html
    assert "2026-07-01" in html


def test_html_escaping_of_case_data(service, context):
    # Case names containing HTML must not end up as markup in the customer mail.
    html = service.render_mail_body("standard.html", context)
    assert "<Alpha>" not in html
    assert "&lt;Alpha&gt;" in html


def test_missing_variable_fails_hard(service, context):
    with pytest.raises(TemplateRenderError, match="broken.html"):
        service.render_mail_body("broken.html", context)


def test_unknown_template_rejected(service, context):
    with pytest.raises(TemplateRenderError):
        service.render_mail_body("does_not_exist.html", context)


def test_path_traversal_rejected(service, context):
    with pytest.raises(TemplateRenderError):
        service.render_mail_body("../../etc/passwd", context)


def test_subject_rendering_and_header_safety(service, context):
    subject = service.render_subject("Report – {{ case.name }}\nInjected: x", context)
    assert "Case" in subject
    assert "\n" not in subject  # no header injection


def test_allowlist_filters_templates(raw_config):
    raw_config["allowed_mail_templates"] = "standard.html"
    service = TemplateService(load_config(raw_config))
    assert service.list_mail_templates() == ["standard.html"]
