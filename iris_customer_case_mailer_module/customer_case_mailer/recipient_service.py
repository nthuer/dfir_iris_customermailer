"""Recipient service: recipient resolution and validation.

Rules (see README):
1. ``To`` is derived from the **contacts configured on the customer**
   (IRIS: Customer -> Contacts). Only contacts whose "Contact Role"
   matches one of ``customer_contact_roles`` (default ``CISO``) and
   that carry an email address are addressed.
2. Role matching is case-insensitive and whitespace-trimmed, but exact
   (``CISO`` matches ``ciso`` / `` CISO ``, not ``Deputy CISO``).
3. Contacts without an email address are skipped silently.
4. Contacts with an *invalid* email address are skipped as well, but
   are reported so they can be documented in the note and the UI.
5. If no contact with the configured role has a valid email address,
   the send is blocked with an error.
6. Deduplication is case-insensitive (first spelling wins).
7. CC/BCC come exclusively from the module configuration; an invalid
   address there blocks the send (it is a configuration error).
8. In test mode, To/CC/BCC are replaced by the test recipients.

Recipients are ALWAYS assembled server-side – frontend input is never
used for recipient lists.
"""

from __future__ import annotations

import re
from typing import List, Optional

from .errors import RecipientError
from .models import CustomerContact, MailerConfig, ResolvedRecipients

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


def select_contacts_by_role(contacts: List[CustomerContact],
                            roles: List[str]) -> List[CustomerContact]:
    """Contacts whose role matches one of ``roles`` (case-insensitive, exact)."""
    wanted = {role.strip().lower() for role in roles if role.strip()}
    return [c for c in contacts if (c.role or "").strip().lower() in wanted]


def resolve_recipients(contacts: List[CustomerContact],
                       config: MailerConfig,
                       force_test_send: bool = False) -> ResolvedRecipients:
    """Assembles the final recipients server-side from the customer contacts.

    Raises :class:`RecipientError` when the customer has no contact with
    the configured role and a valid email address, or when CC/BCC from
    the configuration contain an invalid address. Production recipients
    are resolved even when test mode is active – a test send is meant to
    surface configuration errors, not to hide them.
    """
    role_label = config.contact_roles_label()

    if not contacts:
        raise RecipientError(
            f"No contacts are configured for this customer. Please add a "
            f"contact with role '{role_label}' and an email address to the "
            f"customer in IRIS. Send blocked."
        )

    role_contacts = select_contacts_by_role(contacts, config.customer_contact_roles)
    if not role_contacts:
        raise RecipientError(
            f"The customer has no contact with role '{role_label}'. "
            f"Send blocked."
        )

    with_email = [c for c in role_contacts if (c.email or "").strip()]
    if not with_email:
        raise RecipientError(
            f"No contact with role '{role_label}' has an email address "
            f"configured. Send blocked."
        )

    valid: List[str] = []
    skipped: List[str] = []
    for contact in with_email:
        email = contact.email.strip()
        if is_valid_email(email):
            valid.append(email)
        else:
            skipped.append(contact.display())

    to = dedupe_case_insensitive(valid)
    if not to:
        raise RecipientError(
            f"No contact with role '{role_label}' has a valid email address. "
            f"Skipped invalid: {', '.join(skipped)}. Send blocked."
        )

    cc = dedupe_case_insensitive(list(config.default_cc))
    bcc = dedupe_case_insensitive(list(config.default_bcc))
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
            skipped_invalid=skipped,
            original_to=to, original_cc=cc, original_bcc=bcc,
        )

    return ResolvedRecipients(to=to, cc=cc, bcc=bcc, skipped_invalid=skipped)
