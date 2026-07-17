"""SMTP service: builds the MIME mail and sends it.

Behaviour:
- Port 465  -> implicit TLS (SMTPS).
- otherwise -> plaintext connection, STARTTLS when ``smtp_use_tls``.
- Login only when a username is configured (otherwise anonymous send).
- BCC recipients only appear in the SMTP envelope, never in a header.
- All errors are mapped to typed :class:`SmtpError` subclasses so UI
  and notes show understandable messages.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .errors import (
    SmtpAuthError,
    SmtpConnectError,
    SmtpSendError,
    SmtpTimeoutError,
    SmtpTlsError,
)
from .models import MailerConfig, ReportArtifact, ResolvedRecipients

_FALLBACK_TEXT = (
    "This message contains HTML content. "
    "Please use an email client capable of displaying HTML."
)


class SmtpService:

    def __init__(self, config: MailerConfig, logger):
        self._config = config
        self._logger = logger

    def build_message(self, recipients: ResolvedRecipients, subject: str,
                      html_body: str, artifact: ReportArtifact) -> EmailMessage:
        cfg = self._config
        msg = EmailMessage()
        if cfg.smtp_from_name:
            msg["From"] = formataddr((cfg.smtp_from_name, cfg.smtp_from_address))
        else:
            msg["From"] = cfg.smtp_from_address
        msg["To"] = ", ".join(recipients.to)
        if recipients.cc:
            msg["Cc"] = ", ".join(recipients.cc)
        # Deliberately NOT setting a BCC header – envelope only (send_message).
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()

        msg.set_content(_FALLBACK_TEXT)
        msg.add_alternative(html_body, subtype="html")

        maintype, _, subtype = artifact.mimetype.partition("/")
        msg.add_attachment(
            artifact.content,
            maintype=maintype,
            subtype=subtype,
            filename=artifact.filename,
        )
        return msg

    def send(self, recipients: ResolvedRecipients, subject: str,
             html_body: str, artifact: ReportArtifact) -> None:
        """Sends the mail. Raises typed SmtpError on problems."""
        cfg = self._config
        msg = self.build_message(recipients, subject, html_body, artifact)
        envelope = recipients.all_envelope()
        tls_context = ssl.create_default_context()

        try:
            if cfg.smtp_port == 465:
                # Implicit TLS – TLS errors already occur at connect time.
                smtp = smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port,
                                        timeout=cfg.smtp_timeout_seconds,
                                        context=tls_context)
            else:
                smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port,
                                    timeout=cfg.smtp_timeout_seconds)
        except (socket.timeout, TimeoutError) as exc:
            raise SmtpTimeoutError(
                f"Timeout while connecting to {cfg.smtp_host}:{cfg.smtp_port}.",
                details=repr(exc))
        except ssl.SSLError as exc:
            raise SmtpTlsError(
                f"TLS error while connecting to {cfg.smtp_host}:{cfg.smtp_port}: {exc}.",
                details=repr(exc))
        except (OSError, smtplib.SMTPException) as exc:
            raise SmtpConnectError(
                f"SMTP server {cfg.smtp_host}:{cfg.smtp_port} is not reachable: {exc}.",
                details=repr(exc))

        try:
            with smtp:
                if cfg.smtp_use_tls and cfg.smtp_port != 465:
                    try:
                        smtp.starttls(context=tls_context)
                    except (ssl.SSLError, smtplib.SMTPException) as exc:
                        raise SmtpTlsError(
                            f"STARTTLS with {cfg.smtp_host} failed: {exc}.",
                            details=repr(exc))

                if cfg.smtp_username:
                    try:
                        smtp.login(cfg.smtp_username, cfg.smtp_password or "")
                    except smtplib.SMTPAuthenticationError as exc:
                        raise SmtpAuthError(
                            "SMTP authentication failed. Please check "
                            "username/password in the module configuration.",
                            details=f"SMTP code {exc.smtp_code}")
                    except smtplib.SMTPException as exc:
                        raise SmtpAuthError(
                            f"SMTP authentication not possible: {exc}.",
                            details=repr(exc))

                try:
                    smtp.send_message(msg, from_addr=cfg.smtp_from_address,
                                      to_addrs=envelope)
                except (socket.timeout, TimeoutError) as exc:
                    raise SmtpTimeoutError("Timeout while sending the email.",
                                           details=repr(exc))
                except smtplib.SMTPException as exc:
                    raise SmtpSendError(
                        f"Email was rejected by the server: {exc}.",
                        details=repr(exc))
        except (socket.timeout, TimeoutError) as exc:
            raise SmtpTimeoutError("Timeout in the SMTP session.", details=repr(exc))

        self._logger.info(
            "Email sent to %s recipient(s) via %s:%s (TLS=%s, Auth=%s)"
            % (len(envelope), cfg.smtp_host, cfg.smtp_port,
               cfg.smtp_use_tls or cfg.smtp_port == 465, bool(cfg.smtp_username)))
