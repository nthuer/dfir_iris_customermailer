"""Tests: recipient resolution from customer contacts.

Covers role selection (CISO), skipping of contacts without/with invalid
email, dedupe, CC/BCC merging and the test mode override.
"""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.config_service import load_config
from iris_customer_case_mailer_module.customer_case_mailer.errors import RecipientError
from iris_customer_case_mailer_module.customer_case_mailer.recipient_service import (
    dedupe_case_insensitive,
    parse_address_csv,
    resolve_recipients,
    select_contacts_by_role,
)

from conftest import contact


@pytest.fixture
def config(raw_config):
    return load_config(raw_config)


def test_single_ciso_recipient(config):
    recipients = resolve_recipients([contact("Jane Doe", "ciso@example.org")], config)
    assert recipients.to == ["ciso@example.org"]
    assert recipients.cc == ["lead@example.org"]
    assert recipients.bcc == ["archive@example.org"]
    assert recipients.test_mode_active is False
    assert recipients.skipped_invalid == []


def test_multiple_ciso_recipients(config):
    recipients = resolve_recipients([
        contact("Jane Doe", "ciso1@example.org"),
        contact("Jim Roe", "ciso2@example.org"),
    ], config)
    assert recipients.to == ["ciso1@example.org", "ciso2@example.org"]


def test_only_ciso_role_is_addressed(config):
    recipients = resolve_recipients([
        contact("Jane Doe", "ciso@example.org", "CISO"),
        contact("John Ops", "soc@example.org", "SOC Lead"),
        contact("Ann Legal", "legal@example.org", "Legal"),
    ], config)
    assert recipients.to == ["ciso@example.org"]


def test_role_match_is_case_insensitive_and_trimmed(config):
    recipients = resolve_recipients([
        contact("Lower", "a@example.org", "ciso"),
        contact("Spaced", "b@example.org", "  CISO  "),
        contact("Mixed", "c@example.org", "CiSo"),
    ], config)
    assert recipients.to == ["a@example.org", "b@example.org", "c@example.org"]


def test_role_match_is_exact_not_substring(config):
    # 'Deputy CISO' is deliberately NOT matched by the role 'CISO'.
    with pytest.raises(RecipientError, match="no contact with role 'CISO'"):
        resolve_recipients([contact("Deputy", "dep@example.org", "Deputy CISO")], config)


def test_configurable_roles(raw_config):
    raw_config["customer_contact_roles"] = "CISO, Deputy CISO"
    config = load_config(raw_config)
    recipients = resolve_recipients([
        contact("Chief", "ciso@example.org", "CISO"),
        contact("Deputy", "dep@example.org", "Deputy CISO"),
        contact("Other", "ops@example.org", "SOC Lead"),
    ], config)
    assert recipients.to == ["ciso@example.org", "dep@example.org"]


def test_select_contacts_by_role_helper():
    contacts = [contact("A", "a@example.org", "CISO"),
                contact("B", "b@example.org", "CTO")]
    selected = select_contacts_by_role(contacts, ["ciso"])
    assert [c.name for c in selected] == ["A"]


def test_contact_without_email_is_skipped_silently(config):
    recipients = resolve_recipients([
        contact("No Mail", "", "CISO"),
        contact("Jane Doe", "ciso@example.org", "CISO"),
    ], config)
    assert recipients.to == ["ciso@example.org"]
    assert recipients.skipped_invalid == []


def test_invalid_email_is_skipped_and_reported(config):
    recipients = resolve_recipients([
        contact("Broken", "not-an-email", "CISO"),
        contact("Jane Doe", "ciso@example.org", "CISO"),
    ], config)
    # The valid contact still receives the mail ...
    assert recipients.to == ["ciso@example.org"]
    # ... and the skipped one is reported for the note/UI.
    assert recipients.skipped_invalid == ["Broken <not-an-email>"]


def test_trim_and_dedupe_across_contacts(config):
    recipients = resolve_recipients([
        contact("Jane", " ciso@example.org "),
        contact("Jane again", "CISO@example.org"),
        contact("Second", "ciso2@example.org"),
    ], config)
    # Case-insensitive dedupe, first spelling wins, order stays stable.
    assert recipients.to == ["ciso@example.org", "ciso2@example.org"]


def test_dedupe_helper():
    assert dedupe_case_insensitive(
        ["a@example.org", "A@example.org", "b@example.org"]
    ) == ["a@example.org", "b@example.org"]


def test_parse_address_csv_helper():
    assert parse_address_csv("  a@x.org , ,b@x.org ,") == ["a@x.org", "b@x.org"]


def test_no_contacts_at_all_blocks(config):
    with pytest.raises(RecipientError, match="No contacts are configured"):
        resolve_recipients([], config)


def test_no_ciso_contact_blocks(config):
    with pytest.raises(RecipientError, match="no contact with role 'CISO'"):
        resolve_recipients([contact("John Ops", "soc@example.org", "SOC Lead")], config)


def test_ciso_without_any_email_blocks(config):
    with pytest.raises(RecipientError, match="has an email address"):
        resolve_recipients([contact("Jane Doe", "", "CISO")], config)


def test_all_ciso_emails_invalid_blocks(config):
    with pytest.raises(RecipientError, match="valid email address"):
        resolve_recipients([contact("Broken", "not-an-email", "CISO")], config)


def test_invalid_cc_blocks(raw_config):
    raw_config["default_cc"] = "broken@"
    # An invalid CC address is already caught by the config validation.
    from iris_customer_case_mailer_module.customer_case_mailer.errors import ConfigError
    with pytest.raises(ConfigError, match="default_cc"):
        load_config(raw_config)


def test_test_mode_overrides_recipients(raw_config):
    raw_config["test_mode_enabled"] = True
    raw_config["test_mode_recipients"] = "qa1@example.org,qa2@example.org"
    config = load_config(raw_config)
    recipients = resolve_recipients([contact("Jane Doe", "ciso@example.org")], config)
    assert recipients.test_mode_active is True
    assert recipients.to == ["qa1@example.org", "qa2@example.org"]
    assert recipients.cc == [] and recipients.bcc == []
    assert recipients.original_to == ["ciso@example.org"]


def test_forced_test_send(config):
    recipients = resolve_recipients([contact("Jane Doe", "ciso@example.org")],
                                    config, force_test_send=True)
    assert recipients.test_mode_active is True
    assert recipients.to == ["test@example.org"]
