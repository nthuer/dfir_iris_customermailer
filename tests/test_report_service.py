"""Tests: investigation report rendering (DOCX/HTML, format rules)."""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.errors import ReportRenderError
from iris_customer_case_mailer_module.customer_case_mailer.report_service import ReportService


@pytest.fixture
def case_ctx(fake_adapter):
    return fake_adapter.get_case_context(1, "contact_emails")


@pytest.fixture
def service(raw_config, fake_adapter):
    return ReportService(load_config(raw_config), fake_adapter)


def test_render_docx(service, case_ctx):
    artifact = service.render(case_ctx, "1", "docx", user_id=7)
    assert artifact.content == b"PK-DOCX-REPORT"
    assert artifact.filename.endswith(".docx")
    assert "SOC-2026-0042" in artifact.filename
    assert artifact.mimetype.startswith("application/vnd.openxmlformats")
    assert artifact.template_name == "Standard Investigation"


def test_render_html(service, case_ctx):
    artifact = service.render(case_ctx, "HTML Investigation", "html", user_id=7)
    assert artifact.content == b"<html>REPORT</html>"
    assert artifact.filename.endswith(".html")
    assert artifact.mimetype == "text/html"


def test_format_mismatch_blocks(service, case_ctx):
    # Template 1 is a DOCX template and must not be requested as HTML.
    with pytest.raises(ReportRenderError, match="cannot be rendered as"):
        service.render(case_ctx, "1", "html", user_id=7)


def test_disallowed_format_blocks(raw_config, fake_adapter, case_ctx):
    raw_config["allowed_report_formats"] = "docx"
    raw_config["default_report_format"] = "docx"
    service = ReportService(load_config(raw_config), fake_adapter)
    with pytest.raises(ReportRenderError, match="not enabled"):
        service.render(case_ctx, "2", "html", user_id=7)


def test_template_allowlist(raw_config, fake_adapter):
    raw_config["allowed_report_templates"] = "Standard Investigation"
    service = ReportService(load_config(raw_config), fake_adapter)
    names = [t["name"] for t in service.list_templates()]
    assert names == ["Standard Investigation"]


def test_unknown_template_blocks(service, case_ctx):
    with pytest.raises(ReportRenderError, match="not"):
        service.render(case_ctx, "999", "docx", user_id=7)
