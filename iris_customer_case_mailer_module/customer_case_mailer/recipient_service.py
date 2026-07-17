"""Recipient service: recipient resolution and validation.

Rules (see README):
1. ``To`` comes exclusively from the customer custom attribute
   (default ``contact_emails``), comma-separated.
2. Whitespace is trimmed, empty values are removed.
3. All addresses (To/CC/BCC) are validated – a single invalid address
   blocks the send.
4. Deduplication is case-insensitive (first spelling wins).
5. CC/BCC come exclusively from the module configuration.
6. In test mode, To/CC/BCC are replaced by the test recipients.

Recipients are ALWAYS assembled server-side – frontend input is never
used for recipient lists.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .errors import RecipientError
from .models import MailerConfig, ResolvedRecipients

# Pragmatic RFC 5322 subset: local part + FQDN with at least one TLD.
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def parse_address_csv(raw: Optional[str]) -> List[str]:
    """Splits a CSV string into trimmed, non-empty single addresses."""
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def is_valid_email(address: str) -> bool:
    return bool(_EMAIL_RE.match(address))


def dedupe_case_insensitive(addresses: List[str]) -> List[str]:
    """Removes duplicates case-insensitively, order stays stable."""
    seen = set()
    result = []
    for addr in addresses:
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            result.append(addr)
    return result


def validate_addresses(addresses: List[str], field_name: str) -> None:
    """Raises RecipientError if at least one address is invalid."""
    invalid = [a for a in addresses if not is_valid_email(a)]
    if invalid:
        raise RecipientError(
            f"Invalid email address(es) in {field_name}: {', '.join(invalid)}. "
            f"Send blocked."
        )


def resolve_recipients(contact_emails_raw: Optional[str],
                       config: MailerConfig,
                       force_test_send: bool = False) -> ResolvedRecipients:
    """Assembles the final recipients server-side.

    Raises :class:`RecipientError` for a missing/empty attribute or
    invalid addresses in To, CC or BCC. The production recipients are
    validated even when test mode is active – a test send is meant to
    surface configuration errors, not to hide them.
    """
    if contact_emails_raw is None:
        raise RecipientError(
            f"The customer custom attribute '{config.customer_email_attribute}' "
            f"is not set. Send blocked."
        )

    to = dedupe_case_insensitive(parse_address_csv(contact_emails_raw))
    if not to:
        raise RecipientError(
            f"The customer custom attribute '{config.customer_email_attribute}' "
            f"contains no recipient address. Send blocked."
        )

    cc = dedupe_case_insensitive(list(config.default_cc))
    bcc = dedupe_case_insensitive(list(config.default_bcc))

    validate_addresses(to, "To")
    validate_addresses(cc, "CC")
    validate_addresses(bcc, "BCC")

    test_mode_active = config.test_mode_enabled or force_test_send
    if test_mode_active:
        test_to = dedupe_case_insensitive(list(config.test_mode_recipients))
        if not test_to:
            raise RecipientError(
                "Test mode/test send is active, but no test recipients are "
                "configured (test_mode_recipients). Send blocked."
            )
        validate_addresses(test_to, "test recipients")
        return ResolvedRecipients(
            to=test_to, cc=[], bcc=[],
            test_mode_active=True,
            original_to=to, original_cc=cc, original_bcc=bcc,
        )

    return ResolvedRecipients(to=to, cc=cc, bcc=bcc)
