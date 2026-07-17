"""Send dialog: Flask blueprint inside the IRIS webapp.

ASSUMPTION (documented in the README): the IRIS hook framework cannot
open parameterised modal dialogs. Because modules run inside the IRIS
webapp process, this module registers its own blueprint under
``/customer_case_mailer/...`` at load time. All endpoints require an
authenticated IRIS session (IRIS access control decorators, with a
fallback to flask_login). Without an auth mechanism the dialog is NOT
registered (fail closed).

Security:
- Recipients are assembled exclusively server-side; the frontend only
  provides the subject override and the template/format selection,
  which are re-validated server-side.
- Error responses only contain ``user_message`` (no stack traces, no
  secrets).
"""

from __future__ import annotations

import functools
import os

from ..audit_service import get_logger
from ..config_service import raw_config_from_iris
from ..errors import MailerError
from ..iris_adapter import IrisAdapter
from ..mailer import CustomerCaseMailer
from ..models import SendSelection

_BLUEPRINT_FLAG = "_customer_case_mailer_ui_registered"
_logger = get_logger("iris_customer_case_mailer.ui")


def _load_auth_decorator():
    """Load the IRIS API auth decorator (path varies per IRIS version)."""
    for module_path in ("app.blueprints.access_controls", "app.util"):
        try:
            import importlib
            mod = importlib.import_module(module_path)
            decorator = getattr(mod, "ac_api_requires", None)
            if decorator is not None:
                return decorator
        except ImportError:
            continue

    # Fallback: flask_login session check (fail closed when missing).
    try:
        from flask import jsonify
        from flask_login import current_user

        def requires_login(*_args, **_kwargs):
            def decorate(func):
                @functools.wraps(func)
                def wrapper(*args, **kwargs):
                    if not getattr(current_user, "is_authenticated", False):
                        return jsonify({"success": False,
                                        "message": "Not authenticated."}), 401
                    kwargs.pop("caseid", None)
                    return func(*args, **kwargs)
                return wrapper
            return decorate
        return requires_login
    except ImportError:
        return None


def _build_mailer() -> CustomerCaseMailer:
    adapter = IrisAdapter()
    raw = raw_config_from_iris(adapter.get_module_raw_config())
    return CustomerCaseMailer(raw, adapter=adapter, logger=_logger)


def _current_user_info():
    try:
        from flask_login import current_user
        user_id = getattr(current_user, "id", None)
        display = getattr(current_user, "user", None) or getattr(current_user, "name", None)
        return user_id, (f"{display} (id {user_id})" if display else f"user id {user_id}")
    except ImportError:
        return None, "unknown"


def _selection_from_payload(payload: dict) -> SendSelection:
    return SendSelection(
        mail_template=str(payload.get("mail_template") or ""),
        report_template=str(payload.get("report_template") or ""),
        report_format=str(payload.get("report_format") or ""),
        subject_override=payload.get("subject") or None,
        test_send=bool(payload.get("test_send")),
    )


def register_ui(flask_app=None) -> bool:
    """Registers the dialog blueprint on the IRIS Flask app (idempotent)."""
    try:
        from flask import Blueprint, Response, jsonify, request
        if flask_app is None:
            from app import app as flask_app  # IRIS webapp
    except ImportError as exc:
        _logger.info("Flask/IRIS app not available, dialog is not "
                     "registered: %s" % exc)
        return False

    if flask_app.config.get(_BLUEPRINT_FLAG):
        return True

    auth = _load_auth_decorator()
    if auth is None:
        _logger.error("No IRIS auth mechanism found – for security reasons "
                      "the dialog is NOT registered (fail closed).")
        return False

    bp = Blueprint("customer_case_mailer", __name__,
                   url_prefix="/customer_case_mailer")

    def _json_error(exc: MailerError, status: int = 400):
        return jsonify({"success": False, "message": exc.user_message}), status

    @bp.route("/dialog", methods=["GET"])
    @auth()
    def dialog(**_kwargs):
        html_path = os.path.join(os.path.dirname(__file__), "templates",
                                 "dialog.html")
        with open(html_path, "r", encoding="utf-8") as fh:
            return Response(fh.read(), mimetype="text/html")

    @bp.route("/api/state", methods=["GET"])
    @auth()
    def state(**_kwargs):
        try:
            case_id = int(request.args.get("cid", "0"))
            mailer = _build_mailer()
            return jsonify({"success": True, "state": mailer.dialog_state(case_id)})
        except MailerError as exc:
            return _json_error(exc)

    @bp.route("/api/preview_mail", methods=["POST"])
    @auth()
    def preview_mail(**_kwargs):
        try:
            payload = request.get_json(force=True, silent=True) or {}
            mailer = _build_mailer()
            preview = mailer.preview_mail(int(payload.get("cid", 0)),
                                          _selection_from_payload(payload))
            return jsonify({"success": True, **preview})
        except MailerError as exc:
            return _json_error(exc)

    @bp.route("/api/preview_report", methods=["POST"])
    @auth()
    def preview_report(**_kwargs):
        try:
            payload = request.get_json(force=True, silent=True) or {}
            user_id, _ = _current_user_info()
            mailer = _build_mailer()
            artifact = mailer.preview_report(int(payload.get("cid", 0)),
                                             _selection_from_payload(payload),
                                             user_id)
            disposition = ("inline" if artifact.report_format == "html"
                           else "attachment")
            return Response(
                artifact.content,
                mimetype=artifact.mimetype,
                headers={"Content-Disposition":
                         f'{disposition}; filename="{artifact.filename}"'})
        except MailerError as exc:
            return _json_error(exc)

    @bp.route("/api/send", methods=["POST"])
    @auth()
    def send(**_kwargs):
        try:
            payload = request.get_json(force=True, silent=True) or {}
            user_id, user_display = _current_user_info()
            mailer = _build_mailer()
            result = mailer.send(int(payload.get("cid", 0)), user_id,
                                 user_display, _selection_from_payload(payload))
            return jsonify({"success": result.success,
                            "message": result.message,
                            "note_id": result.note_id,
                            "note_title": result.note_title})
        except MailerError as exc:
            return _json_error(exc)

    flask_app.register_blueprint(bp)
    flask_app.config[_BLUEPRINT_FLAG] = True
    _logger.info("customer_case_mailer dialog registered at /customer_case_mailer/dialog.")
    return True
