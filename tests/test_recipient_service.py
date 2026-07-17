"""Tests: recipient resolution (CSV, trim, dedupe, validation, test mode)."""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.errors import RecipientError
from iris_customer_case_mailer_module.customer_case_mailer.recipient_service import (
    dedupe_case_insensitive,
    parse_address_csv,
    resolve_recipients,
)


@pytest.fixture
def config(raw_config):
    return load_config(raw_config)


def test_single_recipient(config):
    recipients = resolve_recipients("customer1@example.org", config)
    assert recipients.to == ["customer1@example.org"]
    assert recipients.cc == ["lead@example.org"]
    assert recipients.bcc == ["archive@example.org"]
    assert recipients.test_mode_active is False


def test_multiple_csv_recipients(config):
    recipients = resolve_recipients("customer1@example.org,customer2@example.org", config)
    assert recipients.to == ["customer1@example.org", "customer2@example.org"]


def test_trim_and_dedupe():
    parsed = parse_address_csv("  a@example.org , ,B@example.org,a@example.org ,")
    assert parsed == ["a@example.org", "B@example.org", "a@example.org"]
    deduped = dedupe_case_insensitive(parsed + ["b@EXAMPLE.org"])
    # Case-insensitive: first spelling wins, order stays stable.
    assert deduped == ["a@example.org", "B@example.org"]


def test_trim_dedupe_end_to_end(config):
    recipients = resolve_recipients(
        " customer@example.org ,CUSTOMER@example.org, second@example.org ", config)
    assert recipients.to == ["customer@example.org", "second@example.org"]


def test_invalid_to_blocks(config):
    with pytest.raises(RecipientError, match="To"):
        resolve_recipients("customer@example.org,not-an-email", config)


def test_invalid_cc_blocks(raw_config):
    raw_config["default_cc"] = "broken@"
    # An invalid CC address is already caught by the config validation.
    from iris_customer_case_mailer_module.customer_case_mailer.errors import ConfigError
    with pytest.raises(ConfigError, match="default_cc"):
        load_config(raw_config)


def test_missing_attribute_blocks(config):
    with pytest.raises(RecipientError, match="contact_emails"):
        resolve_recipients(None, config)


def test_empty_attribute_blocks(config):
    with pytest.raises(RecipientError, match="no recipient address"):
        resolve_recipients("  , ,  ", config)


def test_test_mode_overrides_recipients(raw_config):
    raw_config["test_mode_enabled"] = True
    raw_config["test_mode_recipients"] = "qa1@example.org,qa2@example.org"
    config = load_config(raw_config)
    recipients = resolve_recipients("customer@example.org", config)
    assert recipients.test_mode_active is True
    assert recipients.to == ["qa1@example.org", "qa2@example.org"]
    assert recipients.cc == [] and recipients.bcc == []
    assert recipients.original_to == ["customer@example.org"]


def test_forced_test_send(config):
    recipients = resolve_recipients("customer@example.org", config, force_test_send=True)
    assert recipients.test_mode_active is True
    assert recipients.to == ["test@example.org"]
