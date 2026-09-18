"""Tests: IRIS module discovery contract and bundled resources.

These guard the parts that only break *inside* IRIS or only after
packaging, where a unit test of the domain logic would not notice:

- IRIS resolves a module's interface via ``__iris_module_interface``:
  it imports ``<package>.<value>`` and instantiates the class of the
  same name. Package attribute, submodule file name and class name must
  therefore agree.
- The HTML mail templates ship inside the wheel, so the module works
  right after installation without copying files into the containers.
"""

import importlib
import os
import re

from iris_customer_case_mailer_module.customer_case_mailer.config_service import (
    BUNDLED_MAIL_TEMPLATES_DIR,
    load_config,
)
from iris_customer_case_mailer_module.customer_case_mailer.models import (
    CaseContext,
    CustomerContact,
)
from iris_customer_case_mailer_module.customer_case_mailer.template_service import (
    TemplateService,
)

PACKAGE = "iris_customer_case_mailer_module"


def _interface_name() -> str:
    package = importlib.import_module(PACKAGE)
    # getattr with an explicit string: attribute access on a dunder name
    # would be mangled inside a class body.
    return getattr(package, "__iris_module_interface")


def test_package_declares_iris_module_interface():
    assert _interface_name() == "IrisCustomerCaseMailerInterface"


def test_interface_submodule_file_exists():
    """IRIS imports <package>.<__iris_module_interface> - the file must exist."""
    package = importlib.import_module(PACKAGE)
    package_dir = os.path.dirname(package.__file__)
    assert os.path.isfile(os.path.join(package_dir, f"{_interface_name()}.py"))


def test_interface_class_name_matches_submodule_name():
    """IRIS does getattr(submodule, <__iris_module_interface>)."""
    package = importlib.import_module(PACKAGE)
    name = _interface_name()
    source_path = os.path.join(os.path.dirname(package.__file__), f"{name}.py")
    with open(source_path, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert re.search(rf"^class {name}\(", source, re.MULTILINE)


def test_module_conf_is_importable_without_iris():
    """The conf module must not pull in iris_interface - IRIS reads it early."""
    conf = importlib.import_module(f"{PACKAGE}.customer_case_mailer_conf")
    assert conf.module_name == "IrisCustomerCaseMailer"
    assert conf.module_configuration, "module_configuration must not be empty"
    for param in conf.module_configuration:
        for key in ("param_name", "param_human_name", "param_description",
                    "default", "mandatory", "type"):
            assert key in param, f"{param.get('param_name')} misses '{key}'"


def test_bundled_mail_templates_are_shipped():
    assert os.path.isdir(BUNDLED_MAIL_TEMPLATES_DIR)
    templates = [f for f in os.listdir(BUNDLED_MAIL_TEMPLATES_DIR)
                 if f.endswith(".html")]
    assert templates, "no mail template shipped with the package"


def test_config_falls_back_to_bundled_templates(raw_config):
    """Leaving mail_templates_dir empty must use the shipped templates."""
    raw_config.pop("mail_templates_dir")
    config = load_config(raw_config)
    assert config.mail_templates_dir == BUNDLED_MAIL_TEMPLATES_DIR


def test_default_mail_template_name_matches_a_bundled_file():
    """The configured default template must actually exist in the wheel."""
    conf = importlib.import_module(f"{PACKAGE}.customer_case_mailer_conf")
    default_name = next(p["default"] for p in conf.module_configuration
                        if p["param_name"] == "default_mail_template")
    assert os.path.isfile(os.path.join(BUNDLED_MAIL_TEMPLATES_DIR, default_name))


def test_bundled_template_renders_with_case_context(raw_config):
    """The shipped template must render under StrictUndefined."""
    raw_config.pop("mail_templates_dir")
    service = TemplateService(load_config(raw_config))
    context = CaseContext(
        case_id=1, name="Ransomware investigation", description="Details.",
        open_date="2026-07-01", soc_id="SOC-2026-0042",
        customer_name="ACME Corp",
        contacts=[CustomerContact("Jane Doe", "ciso@example.org", "CISO")],
    ).template_context()

    for template_name in service.list_mail_templates():
        html = service.render_mail_body(template_name, context)
        assert "ACME Corp" in html
        assert "SOC-2026-0042" in html
