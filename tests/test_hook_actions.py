"""Tests: manual hook dispatch, setup-failure notes, case send options."""

import pytest

from iris_customer_case_mailer_module.customer_case_mailer.hook_actions import (
    HOOK_PREVIEW,
    HOOK_SEND,
    HOOK_TEST_SEND,
    MANUAL_HOOKS,
    report_setup_failure,
    run_hook,
)
from iris_customer_case_mailer_module.customer_case_mailer.iris_adapter import (
    extract_case_send_options,
)


def test_three_manual_hooks_preview_first():
    assert MANUAL_HOOKS == ("Preview customer report", "Send customer report",
                            "Test send customer report")


def test_preview_hook_previews(mailer, fake_adapter, fake_smtp):
    result = run_hook(mailer, HOOK_PREVIEW, 1, 7, "analyst")
    assert result.success is True
    assert fake_smtp.sent == []
    assert fake_adapter.notes[0]["title"].startswith("PREVIEW – ")


def test_send_hook_sends_after_preview(mailer, fake_smtp):
    run_hook(mailer, HOOK_PREVIEW, 1, 7, "analyst")
    assert run_hook(mailer, HOOK_SEND, 1, 7, "analyst").success is True
    assert fake_smtp.sent[0]["recipients"].to == ["ciso@example.org"]


def test_test_send_hook_uses_test_recipients(mailer, fake_smtp):
    assert run_hook(mailer, HOOK_TEST_SEND, 1, 7, "analyst").success is True
    assert fake_smtp.sent[0]["recipients"].to == ["test@example.org"]


def test_unknown_hook_rejected(mailer):
    with pytest.raises(ValueError):
        run_hook(mailer, "Something else", 1, 7, "analyst")


def test_setup_failure_becomes_visible_note(fake_adapter):
    note_id = report_setup_failure(
        fake_adapter, {"smtp_password": "Hidden123"}, HOOK_SEND, 1, 7, "analyst (id 7)",
        "Mandatory configuration parameter 'smtp_host' is missing (pw Hidden123).")
    assert note_id is not None
    note = fake_adapter.notes[0]
    assert note["title"].startswith("FAILED – Customer mail ")
    assert (1, "Communication") in fake_adapter.created_directories
    assert "smtp_host" in note["content"]
    assert "Advanced -> Modules" in note["content"]
    assert "Hidden123" not in note["content"]


def test_setup_failure_on_preview_hook_is_marked_as_preview(fake_adapter):
    report_setup_failure(fake_adapter, {"notes_directory_name": "Kommunikation"},
                         HOOK_PREVIEW, 1, 7, "analyst", "broken")
    assert fake_adapter.notes[0]["title"].startswith("PREVIEW FAILED – ")
    assert (1, "Kommunikation") in fake_adapter.created_directories


def test_setup_failure_never_raises(fake_adapter):
    fake_adapter.fail_directory = True
    assert report_setup_failure(fake_adapter, {}, HOOK_SEND, 1, 7, "a", "x") is None


def test_case_send_options_are_read_from_custom_attributes():
    attrs = {
        "Customer report": {
            "Mail template": {"type": "input_string", "value": " custom.html "},
            "Report template": {"type": "input_string", "value": "HTML Investigation"},
            "Report format": {"type": "input_select", "value": "html"},
            "Mail subject": {"type": "input_string", "value": ""},   # empty -> default
        },
        "Other tab": {"Unrelated": {"value": "x"}},
    }
    assert extract_case_send_options(attrs) == {
        "mail_template": "custom.html",
        "report_template": "HTML Investigation",
        "report_format": "html",
    }


def test_case_send_options_tolerate_missing_or_odd_structures():
    assert extract_case_send_options(None) == {}
    assert extract_case_send_options({"Tab": "not a dict"}) == {}
    assert extract_case_send_options({"Tab": {"MAIL SUBJECT": "Plain value"}}) == \
        {"subject": "Plain value"}
