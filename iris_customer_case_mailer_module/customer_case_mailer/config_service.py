"""Config layer: loads and validates the module configuration.

Input is the raw configuration dict as IRIS persists it for modules
(param_name -> value). Output is a validated, immutable
:class:`MailerConfig`.

Validation errors are raised as :class:`ConfigError` with a message
that analysts can understand, and are shown in the UI.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .errors import ConfigError
from .models import MailerConfig
from .recipient_service import is_valid_email, parse_address_csv

SUPPORTED_REPORT_FORMATS = ("docx", "html")

# Mail templates shipped inside the wheel. Used when the module
# configuration leaves 'mail_templates_dir' empty, so the module works
# right after installation without copying files into the containers.
BUNDLED_MAIL_TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "mail_templates")


def raw_config_from_iris(module_config: Any) -> Dict[str, Any]:
    """Normalises the IRIS module configuration into a flat dict.

    IRIS stores the configuration as a list of parameter dicts (with
    ``param_name`` and ``value``); some code paths already provide a
    flat dict. Both forms are supported.
    """
    if module_config is None:
        return {}
    if isinstance(module_config, dict):
        return dict(module_config)
    flat: Dict[str, Any] = {}
    for param in module_config:
        if isinstance(param, dict) and "param_name" in param:
            flat[param["param_name"]] = param.get("value", param.get("default"))
    return flat


def _as_bool(value: Any, param: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off", ""):
            return False
    raise ConfigError(f"Configuration parameter '{param}' is not a valid boolean: {value!r}")


def _as_int(value: Any, param: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"Configuration parameter '{param}' is not a valid number: {value!r}")


def _as_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require(raw: Dict[str, Any], param: str) -> str:
    value = _as_str(raw.get(param))
    if value is None:
        raise ConfigError(f"Mandatory configuration parameter '{param}' is missing or empty.")
    return value


def _csv_list(value: Any) -> List[str]:
    """CSV string -> trimmed, non-empty entries (no email validation)."""
    if value is None:
        return []
    return [t.strip() for t in str(value).split(",") if t.strip()]


def _validated_address_list(raw_csv: Optional[str], param: str) -> List[str]:
    addresses = parse_address_csv(raw_csv)
    invalid = [a for a in addresses if not is_valid_email(a)]
    if invalid:
        raise ConfigError(
            f"Invalid email address(es) in configuration parameter "
            f"'{param}': {', '.join(invalid)}"
        )
    return addresses


def load_config(raw: Optional[Dict[str, Any]]) -> MailerConfig:
    """Validates the raw configuration dict and builds a MailerConfig."""
    if not raw:
        raise ConfigError(
            "Module is not configured. Please configure it in the IRIS "
            "module management and restart the IRIS services if necessary."
        )

    smtp_from_address = _require(raw, "smtp_from_address")
    if not is_valid_email(smtp_from_address):
        raise ConfigError(f"Sender address 'smtp_from_address' is invalid: {smtp_from_address}")

    formats_raw = _require(raw, "allowed_report_formats")
    allowed_formats = [f.strip().lower() for f in formats_raw.split(",") if f.strip()]
    unknown = [f for f in allowed_formats if f not in SUPPORTED_REPORT_FORMATS]
    if unknown or not allowed_formats:
        raise ConfigError(
            "Configuration parameter 'allowed_report_formats' may only "
            "contain 'docx' and/or 'html'."
        )

    test_mode_enabled = _as_bool(raw.get("test_mode_enabled"), "test_mode_enabled")
    test_recipients = _validated_address_list(_as_str(raw.get("test_mode_recipients")),
                                              "test_mode_recipients")
    if test_mode_enabled and not test_recipients:
        raise ConfigError(
            "Test mode is enabled, but 'test_mode_recipients' is empty. "
            "Sending is not possible without test recipients."
        )

    default_report_format = (_as_str(raw.get("default_report_format")) or allowed_formats[0]).lower()
    if default_report_format not in allowed_formats:
        raise ConfigError(
            f"'default_report_format' ({default_report_format}) is not part of "
            f"'allowed_report_formats' ({', '.join(allowed_formats)})."
        )

    username = _as_str(raw.get("smtp_username"))
    password = _as_str(raw.get("smtp_password"))
    if password and not username:
        raise ConfigError("'smtp_password' is set, but 'smtp_username' is missing.")

    contact_roles = _csv_list(raw.get("customer_contact_roles")) or ["CISO"]

    return MailerConfig(
        smtp_host=_require(raw, "smtp_host"),
        smtp_port=_as_int(raw.get("smtp_port"), "smtp_port"),
        smtp_use_tls=_as_bool(raw.get("smtp_use_tls"), "smtp_use_tls"),
        smtp_username=username,
        smtp_password=password,
        smtp_from_address=smtp_from_address,
        smtp_from_name=_as_str(raw.get("smtp_from_name")),
        smtp_timeout_seconds=_as_int(raw.get("smtp_timeout_seconds") or 30, "smtp_timeout_seconds"),
        default_cc=_validated_address_list(_as_str(raw.get("default_cc")), "default_cc"),
        default_bcc=_validated_address_list(_as_str(raw.get("default_bcc")), "default_bcc"),
        test_mode_enabled=test_mode_enabled,
        test_mode_recipients=test_recipients,
        customer_contact_roles=contact_roles,
        notes_directory_name=_as_str(raw.get("notes_directory_name")) or "Communication",
        default_subject_template=_require(raw, "default_subject_template"),
        allowed_report_formats=allowed_formats,
        allowed_report_templates=_csv_list(raw.get("allowed_report_templates")),
        allowed_mail_templates=_csv_list(raw.get("allowed_mail_templates")),
        mail_templates_dir=(_as_str(raw.get("mail_templates_dir"))
                            or BUNDLED_MAIL_TEMPLATES_DIR),
        manual_hook_sends_with_defaults=_as_bool(raw.get("manual_hook_sends_with_defaults"),
                                                 "manual_hook_sends_with_defaults"),
        default_mail_template=_as_str(raw.get("default_mail_template")),
        default_report_template=_as_str(raw.get("default_report_template")),
        default_report_format=default_report_format,
    )
