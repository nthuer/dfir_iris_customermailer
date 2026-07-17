"""Error/audit service: logging with secret masking.

Security requirement: the SMTP password (and future secrets) must never
appear in logs, notes or UI error messages. Every text that leaves the
module passes through :func:`mask_secrets`.
"""

from __future__ import annotations

import logging
from typing import Iterable, List, Optional

MASK = "********"


def mask_secrets(text: Optional[str], secrets: Iterable[str]) -> str:
    """Replaces all secret values in ``text`` with a mask."""
    if text is None:
        return ""
    masked = str(text)
    for secret in secrets:
        if secret:
            masked = masked.replace(secret, MASK)
    return masked


class SecretMaskingFilter(logging.Filter):
    """Logging filter that removes secrets from every log line."""

    def __init__(self, secrets: List[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_secrets(record.getMessage(), self._secrets)
        record.args = ()
        return True


def get_logger(name: str = "iris_customer_case_mailer",
               secrets: Optional[List[str]] = None) -> logging.Logger:
    """Module logger with masking filter.

    Inside IRIS the records propagate into the IRIS logging; outside
    (tests) the default handler is sufficient.
    """
    logger = logging.getLogger(name)
    # Remove old filters so a config reload picks up new secrets.
    for f in list(logger.filters):
        if isinstance(f, SecretMaskingFilter):
            logger.removeFilter(f)
    logger.addFilter(SecretMaskingFilter(secrets or []))
    return logger
